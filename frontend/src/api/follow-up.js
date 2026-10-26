import { cleanAnswerText } from "./spoken-answer";
const normalize = (text) =>
  String(text || "")
    .normalize("NFKC")
    .replace(/[\s.!?。]/g, "")
    .toLowerCase();
export function followUpPresentation(result) {
  const questions = [
    ...new Map(
      (result.follow_up_questions || [])
        .filter((text) => typeof text === "string")
        .map(cleanAnswerText)
        .filter(Boolean)
        .map((text) => [normalize(text), text]),
    ).values(),
  ];
  const body = normalize(cleanAnswerText(result.answer));
  const additional = questions.filter(
    (text) => !body.includes(normalize(text)),
  );
  // Offer only an explicit HOT/ICED choice requested by the server. Other
  // clarifications remain text; the client must not invent possible answers.
  const temperature =
    result.status === "needs_review" &&
    questions.some(
      (text) =>
        /(?:\bHOT\b|핫|뜨거운)/i.test(text) &&
        /(?:\bICED\b|아이스|차가운)/i.test(text),
    );
  return { additional, temperature };
}

export function temperatureChoice(text) {
  const value = normalize(text);
  const hot = /(?:핫|hot|뜨겁|뜨거운|따뜻)/i.test(value);
  const iced = /(?:아이스|iced|차갑|차가운)/i.test(value);
  if (hot === iced) return null;
  return hot ? "핫" : "아이스";
}

export function hasFollowUpQuestion(result) {
  return (
    result?.status === "needs_review" &&
    (result.follow_up_questions || []).some(
      (text) => typeof text === "string" && cleanAnswerText(text),
    )
  );
}
