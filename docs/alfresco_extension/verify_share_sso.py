"""Run on the LAB host as alfresco; credentials/tokens stay in memory.
Uses existing backend service credentials and CA. Creates only normal login/SSO
sessions and a replay record. Never signs or changes documents.
"""
import base64
import hashlib
import hmac
import http.cookiejar
from html.parser import HTMLParser
import json
from pathlib import Path
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SHARE = "https://alfresco-lab.test"
FIRMA = "https://firmadoc-lab.test"
CA = "/home/alfresco/firmadoc/certs/clinica-caribe-lab-ca.crt"
# Resolve only within this test process, preserving TLS hostname/SNI verification.
# The server does not have firmadoc-lab.test in its host resolver.
original_resolve = socket.getaddrinfo
def resolve(host, *args, **kwargs):
    if host in ("alfresco-lab.test", "firmadoc-lab.test"):
        host = "192.168.0.10"
    return original_resolve(host, *args, **kwargs)
socket.getaddrinfo = resolve
context = ssl.create_default_context(cafile=CA)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def client(jar, redirects=True):
    handlers = [urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=context),
                urllib.request.HTTPCookieProcessor(jar)]
    if not redirects:
        handlers.append(NoRedirect())
    return urllib.request.build_opener(*handlers)

def request(opener, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        response = opener.open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    return response.status, response.headers, response.read().decode("utf-8"), response.url

def check(condition, name):
    if not condition:
        raise RuntimeError("CHECK_FAILED: " + name)
    print(name + "=true", flush=True)

class SsoForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assertion = None
        self.destination = None
        self.method = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "ssoForm":
            self.destination = attrs.get("action")
            self.method = attrs.get("method")
        if tag == "input" and attrs.get("name") == "assertion":
            self.assertion = attrs.get("value")

def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def main():
    runtime = json.loads(subprocess.check_output(["docker", "inspect", "firmadoc-backend"]))[0]
    env = dict(item.split("=", 1) for item in runtime["Config"]["Env"] if "=" in item)
    username = env.get("ALFRESCO_USERNAME") or env["ALFRESCO_USER"]
    password = env["ALFRESCO_PASSWORD"]
    secret = env["FIRMADOC_SSO_SECRET"]
    jar = http.cookiejar.CookieJar()
    share = client(jar)
    share_no_redirect = client(jar, False)
    node = "11111111-1111-4111-8111-111111111111"
    # Read-only repository search, preferring an actual PDF visible to this account.
    auth = "Basic " + base64.b64encode((username + ":" + password).encode()).decode()
    search_body = json.dumps({"query": {"query": 'TYPE:"cm:content" AND cm:content.mimetype:"application/pdf"'},
                              "paging": {"maxItems": 30, "skipCount": 0}}).encode()
    code, _, body, _ = request(share, SHARE + "/alfresco/api/-default-/public/search/versions/1/search",
                               search_body, {"Content-Type": "application/json", "Authorization": auth})
    entries = json.loads(body).get("list", {}).get("entries", []) if code == 200 else []
    document_can_update = False
    if entries:
        node = entries[0]["entry"]["id"]
        for item in entries:
            candidate = item["entry"]["id"]
            code, _, info, _ = request(
                share, SHARE + "/alfresco/api/-default-/public/alfresco/versions/1/nodes/" +
                candidate + "?include=allowableOperations", headers={"Authorization": auth})
            operations = json.loads(info).get("entry", {}).get("allowableOperations", []) if code == 200 else []
            if "update" in operations:
                node = candidate
                document_can_update = True
                break
    print("document_source=" + ("existing_pdf" if entries else "synthetic_uuid"), flush=True)
    ret = SHARE + "/share/page/document-details?nodeRef=workspace://SpacesStore/" + node
    def url(**overrides):
        params = {"nodeId": "workspace://SpacesStore/" + node, "returnUrl": ret}
        params.update(overrides)
        return SHARE + "/share/page/firmadoc/sso-start?" + urllib.parse.urlencode(params)
    anonymous = client(http.cookiejar.CookieJar(), False)
    code, _, body, _ = request(anonymous, url())
    check((code in (301, 302, 303, 401, 403) or (code == 200 and not body.strip())) and 'name="assertion"' not in body,
          "anonymous_cannot_issue")
    login = urllib.parse.urlencode({"username": username, "password": password,
                                     "success": "/share/page/", "failure": "/share/page/type/login"}).encode()
    code, _, _, final_url = request(share, SHARE + "/share/page/dologin", login,
                                    {"Origin": SHARE, "Referer": SHARE + "/share/page/type/login"})
    check(code == 200 and "/type/login" not in final_url, "share_login_valid")
    code, _, body, _ = request(share, SHARE + "/share/page/firmadoc/whoami")
    check(code == 200 and json.loads(body).get("username") == username, "share_user_present")
    for name, params in [
        ("missing_node_rejected", {"nodeId": ""}),
        ("invalid_node_rejected", {"nodeId": "not-a-uuid"}),
        ("missing_return_rejected", {"returnUrl": ""}),
        ("external_return_rejected", {"returnUrl": "https://evil.example/"}),
        ("lookalike_return_rejected", {"returnUrl": "https://alfresco-lab.test.evil.example/"}),
        ("userinfo_return_rejected", {"returnUrl": "https://alfresco-lab.test@evil.example/"}),
        ("http_return_rejected", {"returnUrl": "http://alfresco-lab.test/"})
    ]:
        code, _, body, _ = request(share_no_redirect, url(**params))
        check(code == 400 and 'name="assertion"' not in body, name)
    code, headers, body, _ = request(share_no_redirect, url(user="untrusted_user", username="untrusted_user"))
    form = SsoForm()
    form.feed(body)
    check(code == 200 and bool(form.assertion), "assertion_generated")
    check(form.destination == FIRMA + "/api/auth/sso/exchange" and form.method == "post", "destination_valid")
    check("no-cache" in headers.get("Cache-Control", "") or "no-store" in headers.get("Cache-Control", ""),
          "assertion_response_not_cached")
    version, payload, signature = form.assertion.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    expected = b64(hmac.new(secret.encode(), (version + "." + payload).encode(), hashlib.sha256).digest())
    check(version == "v1" and hmac.compare_digest(signature, expected), "hmac_valid")
    check(set(claims) == {"iss", "aud", "sub", "iat", "exp", "jti", "nid", "ret"}, "exact_claims")
    check(claims["sub"] == username, "browser_identity_ignored")
    check(claims["iss"] == "alfresco-share" and claims["aud"] == "firmadoc", "issuer_audience_valid")
    check(bool(uuid.UUID(claims["jti"])), "jti_present")
    check(claims["exp"] - claims["iat"] == 60 and claims["iat"] <= time.time() < claims["exp"],
          "token_expiry_valid")
    check(claims["nid"] == node and claims["ret"] == ret, "node_return_preserved")
    print("issuer=alfresco-share\naudience=firmadoc\ndestination_host=firmadoc-lab.test\nreturn_origin=" + SHARE,
          flush=True)
    firma_jar = http.cookiejar.CookieJar()
    firma = client(firma_jar, False)
    def exchange(token):
        return request(firma, form.destination, urllib.parse.urlencode({"assertion": token}).encode(),
                       {"Origin": SHARE, "Referer": SHARE + "/share/"})
    code, headers, _, _ = exchange(form.assertion)
    check(code == 303, "exchange_303")
    target = headers.get("Location", "")
    parsed = urllib.parse.urlsplit(target)
    query = urllib.parse.parse_qs(parsed.query)
    check(parsed.path == "/iniciar" and not parsed.netloc and query.get("nodeId") == [node]
          and query.get("returnUrl") == [ret], "redirect_preserves_document")
    cookies = list(firma_jar)
    check(any(c.name == "firmadoc_session" and c.secure and c.has_nonstandard_attr("HttpOnly") for c in cookies),
          "secure_httponly_session_cookie")
    code, _, body, _ = request(firma, FIRMA + "/api/auth/me")
    check(code == 200 and json.loads(body).get("user_id") == username, "firmadoc_identity_matches_share")
    code, _, _, _ = request(firma, FIRMA + target)
    check(code == 200, "iniciar_page_accessible")
    code, _, _, _ = exchange(form.assertion)
    check(code == 400, "replay_rejected")
    def signed(**overrides):
        changed = dict(claims, jti=str(uuid.uuid4()))
        changed.update(overrides)
        body = b64(json.dumps(changed, separators=(",", ":")).encode())
        sig = b64(hmac.new(secret.encode(), ("v1." + body).encode(), hashlib.sha256).digest())
        return "v1." + body + "." + sig
    negative = [
        ("expired_assertion_rejected", signed(iat=int(time.time()) - 30, exp=int(time.time()) - 1)),
        ("wrong_issuer_rejected", signed(iss="other")),
        ("wrong_audience_rejected", signed(aud="other")),
        ("invalid_signature_rejected", version + "." + payload + "." +
         ("A" if signature[0] != "A" else "B") + signature[1:])
    ]
    for name, token in negative:
        code, _, _, _ = exchange(token)
        check(code == 400, name)
    if entries:
        code, _, body, _ = request(share, ret)
        check(code == 200 and "192.168.0.112" not in body, "document_page_without_legacy_ip")
        if "/share/page/firmadoc/sso-start" in body:
            check(True, "rendered_document_action_uses_sso")
        else:
            check(not document_can_update, "sign_action_hidden_for_readonly_account")
            print("button_visual_check=pending_user_with_Write", flush=True)
    import xml.etree.ElementTree as ET
    source = Path("/home/alfresco/alfresco-lab/acs-deployment/docker-compose/config/share/share-config-custom.firmadoc.xml")
    active = subprocess.check_output(["docker", "exec", "docker-compose-share-1", "cat",
        "/usr/local/tomcat/shared/classes/alfresco/web-extension/share-config-custom.xml"])
    check(active == source.read_bytes(), "active_config_matches_persistent_source")
    action = ET.fromstring(active).find(".//action[@id='document-sign-firmadoc']")
    check(action is not None and action.find("param[@name='href']").text ==
          "/share/page/firmadoc/sso-start?nodeId={node.nodeRef}&returnUrl=https://alfresco-lab.test/share/page/document-details?nodeRef={node.nodeRef}",
          "persistent_action_url_valid")
    check(action.find("permissions/permission").text == "Write", "write_permission_preserved")
    print("secret_value_not_logged=true\ncomplete_token_not_logged=true", flush=True)

if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(str(exc), flush=True)
        raise SystemExit(1)
    except Exception as exc:
        print("PROBE_FAILED: " + type(exc).__name__, flush=True)
        raise SystemExit(1)
