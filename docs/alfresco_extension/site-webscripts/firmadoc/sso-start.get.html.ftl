<html>
  <head>
    <meta charset="UTF-8" />
    <meta name="referrer" content="no-referrer" />
    <title>FirmaDoc SSO</title>
  </head>
  <body onload="document.getElementById('ssoForm').submit()">
    <form id="ssoForm" method="post" action="${destination?html}">
      <input type="hidden" name="assertion" value="${assertion?html}" />
      <noscript><button type="submit">Continuar a FirmaDoc</button></noscript>
    </form>
    <p>Redirigiendo a FirmaDoc...</p>
  </body>
</html>
