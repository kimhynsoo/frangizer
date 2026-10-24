// Speech uses existing answer text only: never invent quantities or recipe steps.
const sentences = (text) =>
  String(text || "")
    .trim()
    .split(/(?<=[.!?。])\s+|\n+/)
    .filter(Boolean);
const internal =
  /^(?:도구\s*사용(?:은|을)?\s*종료하고|확보한\s*근거\s*조각\s*ID만\s*사용해\s*최종\s*초안을)/i;
export function cleanAnswerText(text) {
  return sentences(text)
    .filter(
      (line) =>
        !internal.test(line) &&
        !/^합성\s*시연\s*레시피\s*기준입니다[.!。]?$/.test(line),
    )
    .join(" ");
}
function forSpeech(text) {
  return sentences(cleanAnswerText(text))
    .filter(
      (line) =>
        !/^(?:합성[·\s]*시연|합성|시연)\s*(?:용\s*)?(?:레시피|자료|문서)(?:\s*북)?\s*기준입니다[.!。]?$/.test(
          line,
        ),
    )
    .join(" ");
}
const quantity =
  /\d+(?:\.\d+)?\s*(?:m[lL]|ml|g|kg|L|리터|밀리리터|그램|펌프|회|번|초|분|도|℃)/g;
const quantities = (text) =>
  [...text.matchAll(quantity)].map(([value]) =>
    value.replace(/\s/g, "").toLowerCase(),
  );
const warning =
  /주의|알레르|금지|반드시|덜\s*달|시럽\s*(?:빼|제외)|보관|유통기한|사용하지|넣지\s*(?:마|않)|확인할\s*수\s*없/;
function spokenStyle(text) {
  return text
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\bICED\b/gi, "아이스")
    .replace(/\bHOT\b/gi, "핫")
    .replace(/(\d(?:[\d.]*)?)\s*m[lL]\b/g, "$1 밀리리터")
    .replace(/(\d(?:[\d.]*)?)\s*kg\b/g, "$1 킬로그램")
    .replace(/(\d(?:[\d.]*)?)\s*g\b/g, "$1 그램")
    .replace(/섞습니다|섞는다(?=[.!。]|$)/g, "섞어 주세요")
    .replace(/넣습니다|넣는다(?=[.!。]|$)/g, "넣으세요")
    .replace(/붓습니다|붓는다(?=[.!。]|$)/g, "부으세요")
    .replace(/만듭니다|만든다(?=[.!。]|$)/g, "만들어요")
    .replace(
      /([가-힣])입니다(?=[.!。]|$)/g,
      (_, last) =>
        last + ((last.charCodeAt(0) - 0xac00) % 28 ? "이에요" : "예요"),
    )
    .replace(/\s+/g, " ")
    .trim();
}
export function spokenAnswer(result, question = "") {
  const answer = forSpeech(result.answer);
  const steps = (result.steps || []).map(forSpeech).filter(Boolean);
  // Preserve uncertainty/refusal wording instead of turning it into an instruction.
  if (result.status && result.status !== "answered")
    return answer || cleanAnswerText(result.answer);
  let lines = sentences(answer);
  if (/몇\s*펌프|펌프\s*(?:몇|수)/.test(question)) {
    const matches = lines.filter((line) => /펌프/.test(line));
    if (matches.length)
      lines = lines.filter(
        (line) => matches.includes(line) || warning.test(line),
      );
  } else if (/만들|제조|순서|레시피/.test(question) && steps.length) {
    const bodyAmounts = quantities(answer),
      stepAmounts = quantities(steps.join(" "));
    if (
      stepAmounts.length &&
      bodyAmounts.every((amount) => stepAmounts.includes(amount))
    ) {
      lines = [...steps, ...lines.filter((line) => warning.test(line))];
    } else {
      // Keep additional quantities and cautions that exist only in the steps.
      lines.push(
        ...steps.filter(
          (step) =>
            quantities(step).some((amount) => !bodyAmounts.includes(amount)) ||
            warning.test(step),
        ),
      );
    }
  } else if (!answer) lines = steps;
  return (
    spokenStyle([...new Set(lines)].join(" ")) ||
    "답변 내용을 확인하지 못했어요. 화면의 근거를 확인해 주세요"
  );
}
