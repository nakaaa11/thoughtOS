import json
import time
from pathlib import Path

import anthropic


class ClaudeClient:
    def __init__(self, api_key: str, model: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self._input_tokens = 0
        self._output_tokens = 0

    def query(
        self,
        prompt: str,
        system: str = "",
        max_retries: int = 3,
        json_mode: bool = False,
    ) -> str:
        for attempt in range(max_retries):
            try:
                kwargs = {
                    "model": self.model,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if system:
                    kwargs["system"] = system

                response = self.client.messages.create(**kwargs)

                self._input_tokens += response.usage.input_tokens
                self._output_tokens += response.usage.output_tokens

                text = response.content[0].text

                if json_mode:
                    # JSON部分を抽出して検証
                    json.loads(self._extract_json(text))

                return text

            except (anthropic.RateLimitError, anthropic.APIConnectionError):
                wait = 2 ** (attempt + 1)
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                # 529 Overloaded や 5xx 系サーバーエラーはリトライ
                if e.status_code >= 500 or e.status_code == 529:
                    wait = 2 ** (attempt + 1)
                    time.sleep(wait)
                else:
                    raise
            except json.JSONDecodeError:
                if attempt == max_retries - 1:
                    raise
                continue

        raise RuntimeError(f"Failed after {max_retries} retries")

    def query_json(self, prompt: str, system: str = "") -> dict | list | None:
        try:
            text = self.query(prompt, system=system, json_mode=True)
            return json.loads(self._extract_json(text))
        except (json.JSONDecodeError, RuntimeError):
            return None

    def load_prompt(self, template_path: Path, **kwargs) -> str:
        template = template_path.read_text(encoding="utf-8")
        return template.format(**kwargs)

    def usage_summary(self) -> dict:
        # Sonnet pricing: $3/M input, $15/M output
        input_cost = self._input_tokens * 3.0 / 1_000_000
        output_cost = self._output_tokens * 15.0 / 1_000_000
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "estimated_cost_usd": round(input_cost + output_cost, 4),
        }

    def _extract_json(self, text: str) -> str:
        """テキストからJSON部分を抽出"""
        text = text.strip()
        # ```json ... ``` ブロックを探す
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            return text[start:end].strip()
        if "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            return text[start:end].strip()

        # [ または { から始まる部分を探す
        for i, c in enumerate(text):
            if c in ("[", "{"):
                # 対応する閉じ括弧を探す
                depth = 0
                target_close = "]" if c == "[" else "}"
                for j in range(i, len(text)):
                    if text[j] == c:
                        depth += 1
                    elif text[j] == target_close:
                        depth -= 1
                        if depth == 0:
                            return text[i : j + 1]
                break

        return text
