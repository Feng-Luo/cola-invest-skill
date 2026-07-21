"""跨层共享的环境变量名常量。

仅保留 SEC 下载器所需的 SEC_USER_AGENT_ENV。原 dayu.contracts.env_keys 还
包含 TAVILY / SERPER / FMP / FINS_PROCESSOR_PROFILE 等与财报下载无关的常量，
本 vendor 不携带。
"""

SEC_USER_AGENT_ENV = "SEC_USER_AGENT"
"""SEC 下载请求使用的 User-Agent 环境变量名。

SEC 公平访问规则要求自动化请求在 User-Agent 中标识身份并提供联系邮箱，
否则可能被限流或封禁。格式遵循 SEC 官方示例 ``CompanyName admin@company.com``
（公司名 + 空格 + 联系邮箱，无括号、无版本号），例如：

    export SEC_USER_AGENT="ColaFetch admin@example.com"

注意：此常量的**值** ``"SEC_USER_AGENT"`` 是 SEC 规定的环境变量名，
不可修改（改了会读取不到用户设置的环境变量）。
"""

__all__ = ["SEC_USER_AGENT_ENV"]
