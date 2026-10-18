"""Keep server observation notes out of model history and generated text."""
class SourceNoteFilter:
    """Suppress model-written source-note lines, even across SSE chunk boundaries.

    Only buffer a possible prefix at the beginning of a line; ordinary tokens
    continue streaming immediately. The authoritative note is added afterwards.
    """

    prefixes = ("Relevé du namespace", "Sources disponibles", "Sources available", "Namespace snapshot")

    def __init__(self):
        self.pending = ""
        self.line_start = True
        self.discard_line = False

    def feed(self, text: str) -> str:
        output = []
        for char in text:
            if self.discard_line:
                if char == "\n":
                    self.discard_line = False
                    self.line_start = True
                continue
            if self.line_start:
                self.pending += char
                candidate = self.pending.lstrip(" \t*#_>")
                if not candidate:
                    continue
                if candidate in self.prefixes:
                    self.pending = ""
                    self.discard_line = True
                elif not any(prefix.startswith(candidate) for prefix in self.prefixes):
                    output.append(self.pending)
                    self.pending = ""
                    self.line_start = char == "\n"
            else:
                output.append(char)
                self.line_start = char == "\n"
        return "".join(output)

    def finish(self) -> str:
        pending, self.pending = self.pending, ""
        return pending


def filter_source_notes(text: str) -> str:
    filter_ = SourceNoteFilter()
    return (filter_.feed(text) + filter_.finish()).rstrip()
