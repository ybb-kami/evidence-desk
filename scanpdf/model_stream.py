"""Read content deltas from the local OpenAI-compatible server's event stream."""
import json
import time


def answer_prefix(document):
    """Read only the top-level answer string, even when its last escape is split."""
    decoder = json.JSONDecoder()
    remaining = document.lstrip()
    if not remaining.startswith("{"):
        return ""
    remaining = remaining[1:].lstrip()
    while remaining:
        try:
            key, end = decoder.raw_decode(remaining)
        except ValueError:
            return ""
        remaining = remaining[end:].lstrip()
        if not remaining.startswith(":"):
            return ""
        remaining = remaining[1:].lstrip()
        if key == "answer" and remaining.startswith('"'):
            try:
                value, _ = decoder.raw_decode(remaining)
            except ValueError:
                # An incomplete escape needs at most six more characters.
                for cut in range(min(7, len(remaining))):
                    end = len(remaining) - cut
                    try:
                        value = json.loads(remaining[:end] + '"')
                        break
                    except ValueError:
                        continue
                else:
                    return ""
            # Hold a split UTF-16 surrogate pair until the following delta.
            if value and 0xd800 <= ord(value[-1]) <= 0xdbff:
                value = value[:-1]
            if any(0xd800 <= ord(char) <= 0xdfff for char in value):
                return ""
            return value[:450]
        try:
            _, end = decoder.raw_decode(remaining)
        except ValueError:
            return ""
        remaining = remaining[end:].lstrip()
        if not remaining.startswith(","):
            return ""
        remaining = remaining[1:].lstrip()
    return ""


def read_completion(response, emit, *, limit=65536, seconds=95):
    """Consume SSE frames. A truncated stream never yields a completed answer."""
    consumed = 0
    document = shown = ""
    finish = None
    started = time.monotonic()
    fields = []
    while time.monotonic() - started < seconds:
        line = response.readline(min(16385, limit - consumed + 1))
        if not line:
            break
        consumed += len(line)
        if consumed > limit or len(line) > 16384:
            raise ValueError("local stream size limit")
        line = line.rstrip(b"\r\n")
        if line.startswith(b"data:"):
            fields.append(line[5:].lstrip(b" "))
        if line or not fields:
            continue
        data = b"\n".join(fields)
        fields.clear()
        if data == b"[DONE]":
            if finish is None:
                break
            return {"choices": [{"finish_reason": finish, "message": {"content": document}}]}
        event = json.loads(data)
        choices = event["choices"]
        if choices == [] and "usage" in event:
            continue
        if len(choices) != 1:
            raise ValueError("unexpected local choices")
        choice = choices[0]
        delta = choice["delta"].get("content")
        if delta:
            if not isinstance(delta, str) or finish is not None:
                raise ValueError("unexpected local content")
            document += delta
            prefix = answer_prefix(document)
            if len(prefix) > len(shown) and prefix.startswith(shown):
                emit(prefix[len(shown):])
                shown = prefix
        if choice.get("finish_reason") is not None:
            finish = choice["finish_reason"]
    raise ValueError("incomplete local stream")
