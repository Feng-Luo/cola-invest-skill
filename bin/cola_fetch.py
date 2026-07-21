#!/usr/bin/env python3
"""cola_fetch - 财报下载 CLI。

入口脚本：运行期把 ``bin/`` 插入 ``sys.path``，使
``import download_financial_report.xxx`` 生效。

支持的来源：
    - SEC EDGAR（美股，10-K / 20-F / 10-Q）
    - 港交所披露易（港股，年报 / 半年报 / 季报）
    - 巨潮资讯网（A股，年报 / 半年报 / 一季报 / 三季报）

使用示例：
    python3 cola_fetch.py download --ticker 00700 --annual-years 3
    python3 cola_fetch.py download --ticker 600519 --annual-years 3 --latest-interim
    SEC_USER_AGENT="ColaFetch admin@example.com" \\
        python3 cola_fetch.py download --ticker AAPL --annual-years 3
"""

from __future__ import annotations
# pyright: reportImplicitRelativeImport=false
# 文件作为顶层脚本直接执行（python3 cola_fetch.py），靠运行期 sys.path.insert
# 把 bin/ 纳入搜索路径让 `download_financial_report` 作为顶层包被发现；
# 静态分析器看不到这步，会误报隐式相对导入，故整文件禁用此规则。

import argparse
import asyncio
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# 关键：把 bin/ 插入 sys.path，使 `import download_financial_report.*` 生效。
# 这一步必须在 import download_financial_report 之前完成。
_BIN_DIR = Path(__file__).resolve().parent
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from download_financial_report.contracts.env_keys import SEC_USER_AGENT_ENV
from download_financial_report.fins.domain.document_models import FileObjectMeta
from download_financial_report.fins.downloaders.cninfo_downloader import (
    CninfoDiscoveryClient,
)
from download_financial_report.fins.downloaders.hkexnews_downloader import (
    HkexnewsDiscoveryClient,
)
from download_financial_report.fins.downloaders.sec_downloader import SecDownloader
from download_financial_report.fins.pipelines.cn_download_models import (
    CnFiscalPeriod,
    CnReportQuery,
)
from download_financial_report.fins.ticker_normalization import (
    NormalizedTicker,
    normalize_ticker,
)
from download_financial_report.log import Log

_MODULE = "COLA_FETCH"


class SecThrottled(Exception):
    """SEC 限流（429/503）触发，需用户设置真实 UA 后重跑。

    由 ``_download_sec_async`` 在检测到 ``was_throttled()`` 时抛出，
    ``_cmd_download`` 捕获后返回退出码 ``3``，区别于普通失败的 ``1``。
    """
_SEC_ANNUAL_FORMS = ("10-K", "20-F")
_SEC_QUARTER_FORMS = ("10-Q",)


# ------------------------------------------------------------------
# 工具：年份 / 窗口 / 落盘路径
# ------------------------------------------------------------------


def _resolve_window(now: datetime, annual_years: int) -> tuple[str, str]:
    """生成 CN/HK 查询窗口 ``[start_date, end_date]``。

    Args:
        now: 当前时间。
        annual_years: 目标年报数量。

    Returns:
        ``(start_date, end_date)``，格式 ``YYYY-MM-DD``。

    Raises:
        无。
    """

    end_date = now.strftime("%Y-%m-%d")
    start_year = now.year - max(annual_years, 1) - 1  # 多留 1 年余量
    return f"{start_year}-01-01", end_date


def _is_sec_index_wrapper(name: str) -> bool:
    """判断文件名是否为 SEC 生成的归档包装文件（非正文）。

    SEC 现代 filings 的 index.json 中，每个条目的 ``type`` 字段都被置为通用
    的 ``text.gif``，无法按 form 类型精确匹配。``_select_primary_from_index_items``
    在匹配失败时会兜底返回列表首个 ``.htm/.html/.txt`` 条目，而该条目往往是
    ``<accession>-index-headers.html`` 这类 filing 头文件，而非 10-K/10-Q 正文。
    此类包装文件应被排除，让调用方回退到 submissions API 的权威 ``primaryDocument``。

    Args:
        name: 文件名。

    Returns:
        是包装文件返回 ``True``，否则 ``False``。

    Raises:
        无。
    """
    lowered = (name or "").lower()
    return (
        lowered.endswith("-index-headers.html")
        or lowered == "index.html"
        or lowered.endswith("-index.html")
        or lowered.endswith(".txt")
        or lowered.endswith("xbrl.zip")
    )


def _safe_filename_component(name: str) -> str:
    """清洗公司名为合法文件名片段。

    Args:
        name: 原始公司名。

    Returns:
        去掉非法字符、限制长度后的字符串。

    Raises:
        无。
    """

    if not name:
        return ""
    cleaned = re.sub(r'[\r\n\t/\\:*?"<>|]', "", name).strip()
    cleaned = cleaned.lstrip(".")  # 避免生成隐藏文件（Unix 下 . 开头不可见）
    return cleaned[:80]


def _build_target_path(
    *,
    workspace_root: Path,
    ticker: str,
    company_name: str,
    fiscal_year: int,
    fiscal_period: str,
    suffix: str = ".pdf",
) -> Path:
    """构造落盘路径 ``<workspace_root>/filings/<ticker>/<name>-YYYY-<period><suffix>``。

    公司名解析失败时退化为 ``<ticker>-YYYY-<period><suffix>``。

    Args:
        workspace_root: 工作区根目录。
        ticker: 规范 ticker。
        company_name: 公司名。
        fiscal_year: 财年。
        fiscal_period: 财期（FY / H1 / Q1 / Q2 / Q3 / Q4）。
        suffix: 文件后缀；HK / CN 默认 ``.pdf``，SEC 传主文档实际后缀。

    Returns:
        目标文件路径。父目录会被自动创建。

    Raises:
        无。
    """

    filings_dir = workspace_root / "filings" / ticker
    filings_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename_component(company_name)
    prefix = safe_name if safe_name else ticker
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return filings_dir / f"{prefix}-{fiscal_year}-{fiscal_period}{normalized_suffix}"


def _build_target_periods(
    *,
    annual_years: int,
    latest_interim: bool,
    latest_quarter: bool,
) -> tuple[CnFiscalPeriod, ...]:
    """根据用户参数构造 CN/HK 目标财期集合。

    Args:
        annual_years: 年报数量；>0 时包含 ``FY``。
        latest_interim: 是否包含 ``H1``。
        latest_quarter: 是否包含 ``Q1``–``Q4``。

    Returns:
        财期元组；空集合表示无可下载目标。

    Raises:
        无。
    """

    periods: list[CnFiscalPeriod] = []
    if annual_years > 0:
        periods.append("FY")
    if latest_interim:
        periods.append("H1")
    if latest_quarter:
        periods.extend(["Q1", "Q2", "Q3", "Q4"])
    return tuple(periods)


# ------------------------------------------------------------------
# HK / CN 下载流程
# ------------------------------------------------------------------


def _download_cn_or_hk(
    *,
    normalized: NormalizedTicker,
    workspace_root: Path,
    annual_years: int,
    latest_interim: bool,
    latest_quarter: bool,
) -> int:
    """下载 HK / CN 财报，返回下载成功数。

    Args:
        normalized: 已归一化的 ticker。
        workspace_root: 工作区根目录。
        annual_years: 年报数量。
        latest_interim: 是否拉最新半年报。
        latest_quarter: 是否拉最新季报。

    Returns:
        成功下载的报告数量。

    Raises:
        无（异常会被捕获并记日志，不让整个流程崩）。
    """

    now = datetime.now(timezone.utc)
    start_date, end_date = _resolve_window(now, annual_years)
    target_periods = _build_target_periods(
        annual_years=annual_years,
        latest_interim=latest_interim,
        latest_quarter=latest_quarter,
    )
    if not target_periods:
        Log.warn("无可下载目标 periods", module=_MODULE)
        return 0

    market = "HK" if normalized.market == "HK" else "CN"
    query = CnReportQuery(
        market=market,
        normalized_ticker=normalized.canonical,
        start_date=start_date,
        end_date=end_date,
        target_periods=target_periods,
    )

    client = HkexnewsDiscoveryClient() if market == "HK" else CninfoDiscoveryClient()
    success_count = 0
    fy_taken = 0
    h1_taken = False
    quarter_taken = False

    try:
        profile = client.resolve_company(query)
        Log.info(
            f"解析公司: {profile.company_name} (id={profile.company_id})",
            module=_MODULE,
        )
        candidates = client.list_report_candidates(query, profile)
        Log.info(f"找到 {len(candidates)} 个候选报告", module=_MODULE)

        # candidates 已按 (-fiscal_year, _PERIOD_SORT_KEY[fiscal_period]) 排序：
        # 同 fiscal_year 内 FY 在前、H1 次之、Q4→Q1 由新到旧；fiscal_year 越大越靠前。
        # 因此直接顺序遍历即可优先取最新年报/半年报/季报（季报取最新季度）。
        for cand in candidates:
            if cand.fiscal_period == "FY":
                if fy_taken >= annual_years:
                    continue
                fy_taken += 1
            elif cand.fiscal_period == "H1":
                if not latest_interim or h1_taken:
                    continue
                h1_taken = True
            else:  # Q1-Q4
                if not latest_quarter or quarter_taken:
                    continue
                quarter_taken = True
            target_path = _build_target_path(
                workspace_root=workspace_root,
                ticker=normalized.canonical,
                company_name=profile.company_name,
                fiscal_year=cand.fiscal_year,
                fiscal_period=cand.fiscal_period,
                suffix=".pdf",
            )
            if target_path.exists():
                Log.info(
                    f"已存在，跳过: {target_path.name}",
                    module=_MODULE,
                )
                success_count += 1
                continue
            try:
                asset = client.download_report_pdf(cand)
                shutil.move(str(asset.pdf_path), str(target_path))
                Log.info(
                    f"下载完成: {target_path.name} ({asset.content_length} bytes)",
                    module=_MODULE,
                )
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                Log.warn(
                    f"下载失败 {cand.fiscal_year}-{cand.fiscal_period}: {exc}",
                    module=_MODULE,
                )
    except Exception as exc:  # noqa: BLE001
        Log.error(
            f"HK/CN 下载流程异常（resolve_company / list_report_candidates 等）: "
            f"ticker={normalized.canonical} error={exc}",
            module=_MODULE,
        )
        return success_count
    finally:
        client.close()

    return success_count


# ------------------------------------------------------------------
# SEC 下载流程
# ------------------------------------------------------------------


def _store_sec_file_factory(target_path: Path):
    """构造 SEC ``store_file`` 回调。

    SEC ``download_files`` 接受 ``Callable[[str, BinaryIO], FileObjectMeta]``，
    本函数返回一个把流写入 ``target_path`` 的闭包。

    Args:
        target_path: 落盘目标路径。

    Returns:
        ``store_file`` 回调函数。

    Raises:
        无。
    """

    def store_file(_name: str, stream) -> FileObjectMeta:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("wb") as fp:
            fp.write(stream.read())
        return FileObjectMeta(uri=str(target_path))

    return store_file


async def _download_sec_async(
    *,
    normalized: NormalizedTicker,
    workspace_root: Path,
    annual_years: int,
    latest_quarter: bool,
    sec_user_agent: str | None,
) -> int:
    """下载 SEC 财报，返回下载成功数。

    Args:
        normalized: 已归一化的 ticker（market=US）。
        workspace_root: 工作区根目录。
        annual_years: 年报数量。
        latest_quarter: 是否拉最新季报（10-Q）。
        sec_user_agent: 显式 UA；为 ``None`` 时由 sec_downloader 走环境变量。

    Returns:
        成功下载的报告数量。

    Raises:
        无（异常会被捕获并记日志）。
    """

    downloader = SecDownloader(workspace_root=workspace_root)
    if sec_user_agent:
        downloader.configure(
            user_agent=sec_user_agent,
            sleep_seconds=0.2,
            max_retries=3,
        )

    success_count = 0
    try:
        cik, company_name, cik10 = await downloader.resolve_company(normalized.canonical)
        Log.info(
            f"解析公司: {company_name} (cik={cik})",
            module=_MODULE,
        )
        submissions = await downloader.fetch_submissions(cik10)
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", []) or []
        accession_numbers = recent.get("accessionNumber", []) or []
        filing_dates = recent.get("filingDate", []) or []
        primary_documents = recent.get("primaryDocument", []) or []

        # submissions.recent 已按 filingDate 倒序；顺序遍历即可优先取最新
        # 10-K/20-F 与 10-Q。
        annual_taken = 0
        quarter_taken = False
        targets: list[tuple[str, str, str, str]] = []
        for i, form in enumerate(forms):
            if form in _SEC_ANNUAL_FORMS:
                if annual_years == 0 or annual_taken >= annual_years:
                    continue
                annual_taken += 1
            elif form in _SEC_QUARTER_FORMS:
                if not latest_quarter or quarter_taken:
                    continue
                quarter_taken = True
            else:
                continue
            if i >= len(accession_numbers) or i >= len(primary_documents) or i >= len(filing_dates):
                continue
            targets.append(
                (form, accession_numbers[i], primary_documents[i], filing_dates[i])
            )

        Log.info(f"筛选出 {len(targets)} 个目标 filings", module=_MODULE)

        for form, accession, primary_doc, filing_date in targets:
            year_match = re.match(r"(\d{4})-", filing_date)
            if not year_match:
                Log.warn(
                    f"跳过 {form} {accession}: 无法从 filing_date={filing_date!r} 解析年份",
                    module=_MODULE,
                )
                continue
            fiscal_year = int(year_match.group(1))
            fiscal_period = "FY" if form in _SEC_ANNUAL_FORMS else "Q"
            suffix = Path(primary_doc).suffix or ".htm"

            target_path = _build_target_path(
                workspace_root=workspace_root,
                ticker=normalized.canonical,
                company_name=company_name,
                fiscal_year=fiscal_year,
                fiscal_period=fiscal_period,
                suffix=suffix,
            )
            if target_path.exists():
                Log.info(
                    f"已存在，跳过: {target_path.name}",
                    module=_MODULE,
                )
                success_count += 1
                continue

            try:
                accession_no_dash = accession.replace("-", "")
                resolved_primary = await downloader.resolve_primary_document(
                    cik=cik,
                    accession_no_dash=accession_no_dash,
                    form_type=form,
                )
                # SEC 现代 filings 的 index.json 中每项 type 都被置为通用 "text.gif"，
                # 无法按 form 类型精确匹配，resolve_primary_document 会兜底返回
                # index-headers.html 等系统生成的包装文件而非正文。此时回退到
                # submissions API 的 primaryDocument（权威主文档）。
                if resolved_primary and not _is_sec_index_wrapper(resolved_primary):
                    primary_document = resolved_primary
                else:
                    primary_document = primary_doc
                remote_files = await downloader.list_filing_files(
                    cik=cik,
                    accession_no_dash=accession_no_dash,
                    primary_document=primary_document,
                    form_type=form,
                    include_xbrl=False,
                    include_exhibits=False,
                    include_http_metadata=False,
                )
                primary_descriptors = [
                    rf for rf in remote_files if rf.name == primary_document
                ]
                if not primary_descriptors:
                    Log.warn(
                        f"{form} {accession}: 找不到 primary document",
                        module=_MODULE,
                    )
                    continue
                results = await downloader.download_files(
                    remote_files=primary_descriptors,
                    overwrite=True,
                    store_file=_store_sec_file_factory(target_path),
                )
                if results and results[0].get("status") == "downloaded":
                    Log.info(
                        f"下载完成: {target_path.name}",
                        module=_MODULE,
                    )
                    success_count += 1
                else:
                    Log.warn(
                        f"下载失败 {form} {accession}: {results}",
                        module=_MODULE,
                    )
            except Exception as exc:  # noqa: BLE001
                if downloader.was_throttled():
                    Log.error(
                        "SEC 限流（429/503）触发。预设 UA 已不够用，"
                        "请设置环境变量 SEC_USER_AGENT 为真实 UA（含联系邮箱），格式如：\n"
                        '    export SEC_USER_AGENT="ColaFetch 你的邮箱@example.com"\n'
                        "设置后重跑本命令；港股/A股不受影响。",
                        module=_MODULE,
                    )
                    raise SecThrottled() from exc
                Log.warn(
                    f"处理 {form} {accession} 异常: {exc}",
                    module=_MODULE,
                )
    except SecThrottled:
        # 内层 try 已识别限流并抛出，透传给 _cmd_download 处理
        raise
    except Exception as exc:  # noqa: BLE001
        # 覆盖 resolve_company / fetch_submissions 等内层 try 之外的限流场景：
        # 这两个请求最易在首次接触 SEC 时触发 429/503，原异常会绕过内层 was_throttled() 检查
        if downloader.was_throttled():
            Log.error(
                "SEC 限流（429/503）触发。预设 UA 已不够用，"
                "请设置环境变量 SEC_USER_AGENT 为真实 UA（含联系邮箱），格式如：\n"
                '    export SEC_USER_AGENT="ColaFetch 你的邮箱@example.com"\n'
                "设置后重跑本命令；港股/A股不受影响。",
                module=_MODULE,
            )
            raise SecThrottled() from exc
        raise
    finally:
        await downloader.close()

    return success_count


def _download_sec(
    *,
    normalized: NormalizedTicker,
    workspace_root: Path,
    annual_years: int,
    latest_quarter: bool,
    sec_user_agent: str | None,
) -> int:
    """包装 SEC 异步下载为同步入口。"""

    return asyncio.run(
        _download_sec_async(
            normalized=normalized,
            workspace_root=workspace_root,
            annual_years=annual_years,
            latest_quarter=latest_quarter,
            sec_user_agent=sec_user_agent,
        )
    )


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------


def _cmd_download(args: argparse.Namespace) -> int:
    """``download`` 子命令实现。"""

    try:
        normalized = normalize_ticker(args.ticker)
    except ValueError as exc:
        Log.error(f"无法识别 ticker: {exc}", module=_MODULE)
        return 2

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    sec_user_agent = args.sec_user_agent or os.environ.get(SEC_USER_AGENT_ENV)

    Log.info(
        f"开始下载: ticker={normalized.canonical} market={normalized.market} "
        f"annual_years={args.annual_years} latest_interim={args.latest_interim} "
        f"latest_quarter={args.latest_quarter}",
        module=_MODULE,
    )

    if normalized.market == "US":
        try:
            count = _download_sec(
                normalized=normalized,
                workspace_root=workspace_root,
                annual_years=args.annual_years,
                latest_quarter=args.latest_quarter,
                sec_user_agent=sec_user_agent,
            )
        except SecThrottled:
            return 3  # SEC 限流专用退出码，区别于普通失败(1)/参数错误(2)
    elif normalized.market in ("HK", "CN"):
        count = _download_cn_or_hk(
            normalized=normalized,
            workspace_root=workspace_root,
            annual_years=args.annual_years,
            latest_interim=args.latest_interim,
            latest_quarter=args.latest_quarter,
        )
    else:
        Log.error(f"不支持的市场: {normalized.market}", module=_MODULE)
        return 2

    Log.info(f"下载完成: 共 {count} 份报告", module=_MODULE)
    return 0 if count > 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse parser。"""

    parser = argparse.ArgumentParser(
        prog="cola_fetch",
        description="财报下载 CLI（SEC EDGAR / 港交所披露易 / 巨潮资讯网）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser(
        "download", help="下载指定 ticker 的财报"
    )
    download_parser.add_argument(
        "--ticker",
        required=True,
        help="规范 ticker（如 00700 / 600519 / AAPL / BRK-B）",
    )
    download_parser.add_argument(
        "--annual-years",
        type=int,
        default=3,
        help="年报数量（默认 3）",
    )
    download_parser.add_argument(
        "--latest-interim",
        action="store_true",
        help="同时下载最新半年报（A股 H1 / 港股 H1）",
    )
    download_parser.add_argument(
        "--latest-quarter",
        action="store_true",
        help="同时下载最新季报（A股 Q1/Q3 / 港股 Q1-Q4 / SEC 10-Q）",
    )
    download_parser.add_argument(
        "--workspace-root",
        default=".",
        help="工作区根目录（默认当前目录）",
    )
    download_parser.add_argument(
        "--sec-user-agent",
        help="SEC User-Agent（默认读环境变量 SEC_USER_AGENT）",
    )
    download_parser.set_defaults(func=_cmd_download)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        # argparse 内部 sys.exit，透传不吞
        raise
    except Exception as exc:  # noqa: BLE001
        Log.error(f"未预期异常: {exc}", module=_MODULE)
        return 1


if __name__ == "__main__":
    sys.exit(main())
