export function consumeApprovalToken(locationLike, historyLike) {
  const token = decodeURIComponent(String(locationLike.hash || "").slice(1));
  historyLike.replaceState(null, "", "/autorizar-gerencia");
  return token;
}
