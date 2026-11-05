"""服务商注册表：provider_id → 允许的目标主机白名单，代码内唯一来源。

档案配置只声明 provider_id；真正允许连接哪些主机在这里登记。因此仅修改
configs/model_profiles.json 无法把某个服务商的密钥指向别家域名——必须先改代码。
"""
from urllib.parse import urlsplit


PROVIDERS = {
    'siliconflow': {'hosts': ('api.siliconflow.cn',)},
    # 百炼工作空间子域形如 {workspace}.{region}.maas.aliyuncs.com，前缀由部署决定，
    # 因此按域名后缀登记（前导点表示后缀匹配）。
    'aliyun_maas': {'hosts': ('.maas.aliyuncs.com',)},
}


def registered(provider):
    entry = PROVIDERS.get(provider)
    return isinstance(entry, dict) and bool(entry.get('hosts'))


def hosts(provider):
    if not registered(provider):
        raise ValueError('未登记的服务商')
    return PROVIDERS[provider]['hosts']


def allowed_host(provider, hostname):
    if not registered(provider) or not isinstance(hostname, str) or not hostname:
        return False
    host = hostname.lower().rstrip('.')
    for allowed in hosts(provider):
        allowed = allowed.lower()
        if allowed.startswith('.'):
            if host.endswith(allowed) and host != allowed[1:]:
                return True
        elif host == allowed:
            return True
    return False


def validate_url(provider, url):
    """https + 无凭证/查询/片段 + 主机属于该服务商白名单。"""
    if not isinstance(url, str) or not url:
        return False
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    return allowed_host(provider, parsed.hostname)
