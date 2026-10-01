(function () {
var nodeId = String(args.nodeId || "");
var returnUrl = args.returnUrl || "";
var username = String(user.name || "").trim();

function reject(code, message) {
    status.code = code;
    status.message = message;
    status.redirect = true;
}

if (!/^[A-Za-z0-9._@-]+$/.test(username) || /^(guest|anonymous)$/i.test(username)) {
    reject(401, "Usuario no autenticado en Share");
    return;
}

if (!nodeId) {
    reject(400, "Falta nodeId");
    return;
}

var cleanNodeId = nodeId.replace(/^workspace:\/\/SpacesStore\//, "");
if (!/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$/.test(cleanNodeId)) {
    reject(400, "nodeId inválido");
    return;
}

if (!/^https:\/\/alfresco-lab\.test(?:\/|$)/.test(returnUrl)) {
    reject(400, "returnUrl no permitida");
    return;
}

var now = Math.floor(new Date().getTime() / 1000);
var payload = {
    iss: "alfresco-share",
    aud: "firmadoc",
    sub: username,
    iat: now,
    exp: now + 60,
    jti: String(java.util.UUID.randomUUID().toString()),
    nid: cleanNodeId,
    ret: returnUrl
};

var secret = "";
try {
    var reader = new java.io.BufferedReader(
        new java.io.InputStreamReader(
            new java.io.FileInputStream("/run/secrets/firmadoc-sso-secret"),
            "UTF-8"
        )
    );
    try {
        var secretLine = reader.readLine();
        secret = secretLine ? String(secretLine).trim() : "";
    } finally {
        reader.close();
    }
} catch (secretReadError) {
    reject(500, "Falta la configuracion server-side de firma HMAC");
    return;
}
if (new java.lang.String(secret).getBytes("UTF-8").length < 32) {
    reject(500, "Falta la configuracion server-side de firma HMAC");
    return;
}

var destination = String(java.lang.System.getenv("FIRMADOC_SSO_DESTINATION") || "").trim();
if (destination !== "https://firmadoc-lab.test/api/auth/sso/exchange") {
    reject(500, "Falta la configuracion server-side del destino SSO");
    return;
}

var payloadJson = JSON.stringify(payload);
var payloadB64 = Packages.org.apache.commons.codec.binary.Base64.encodeBase64URLSafeString(new java.lang.String(payloadJson).getBytes("UTF-8"));
var mac = javax.crypto.Mac.getInstance("HmacSHA256");
var signingKey = new javax.crypto.spec.SecretKeySpec(new java.lang.String(secret).getBytes("UTF-8"), "HmacSHA256");
mac.init(signingKey);
mac.update(new java.lang.String("v1." + payloadB64).getBytes("UTF-8"));
var sigB64 = Packages.org.apache.commons.codec.binary.Base64.encodeBase64URLSafeString(mac.doFinal());

model.assertion = "v1." + payloadB64 + "." + sigB64;
model.destination = String(destination).trim();
model.nodeId = cleanNodeId;
model.returnUrl = returnUrl;
}());
