// Store confirmed transcript aliases only; never store recordings or voiceprints.
export const normalizeWakeAlias = (text) =>
  String(text || "")
    .normalize("NFKC")
    .trim()
    .replace(/^헤이\s*/, "")
    .replace(/[\s,.!?。'"“”‘’]/g, "")
    .replace(/야$/, "");
export function validWakeAlias(text) {
  const alias = normalizeWakeAlias(text);
  return (
    /^[가-힣]{2,10}$/.test(alias) &&
    !/^(질문취소|다시말하기|그만하세요)$/.test(alias) &&
    !/^[프브][랜렌랑란]/.test(alias)
  );
}
const key = (account) =>
  `frangizer.wake-aliases.v2.siri.${encodeURIComponent(account)}`;
// Retire every account's former wake-word registrations on this site only.
// Other local storage (login, conversations, preferences) is left intact.
export function resetLegacyWakeProfiles(storage = globalThis.localStorage) {
  try {
    const retired = [];
    for (let i = 0; i < storage.length; i++) {
      const name = storage.key(i);
      if (name?.startsWith("frangizer.wake-aliases.v1.")) retired.push(name);
    }
    retired.forEach((name) => storage.removeItem(name));
    return retired.length;
  } catch {
    return 0;
  }
}
export function loadWakeProfile(account, storage = globalThis.localStorage) {
  resetLegacyWakeProfiles(storage);
  if (!account) return [];
  try {
    const value = JSON.parse(storage.getItem(key(account)) || "null");
    return Array.isArray(value?.aliases)
      ? [
          ...new Set(
            value.aliases.filter(validWakeAlias).map(normalizeWakeAlias),
          ),
        ].slice(0, 5)
      : [];
  } catch {
    return [];
  }
}
export function saveWakeProfile(
  account,
  aliases,
  storage = globalThis.localStorage,
) {
  if (!account) throw new Error("로그인 계정을 확인해 주세요");
  const confirmed = [
    ...new Set(aliases.filter(validWakeAlias).map(normalizeWakeAlias)),
  ].slice(0, 5);
  if (!confirmed.length)
    throw new Error("호출어로 사용할 표현을 하나 이상 선택해 주세요");
  try {
    storage.setItem(key(account), JSON.stringify({ aliases: confirmed }));
  } catch {
    throw new Error(
      "이 브라우저에 저장하지 못했습니다. 저장 공간과 브라우저 설정을 확인해 주세요",
    );
  }
  return confirmed;
}
export function clearWakeProfile(account, storage = globalThis.localStorage) {
  try {
    if (account) storage.removeItem(key(account));
  } catch {
    throw new Error("저장된 발음을 초기화하지 못했습니다.");
  }
}
