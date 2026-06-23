def chunk_text(text: str, max_chars: int = 20000) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_chars:
            chunks.append(text.strip())
            break

        window = text[:max_chars]
        # prefer paragraph breaks, then line breaks, then sentence ends,
        # so clauses are not split mid-thought across chunks
        cut = window.rfind("\n\n")
        if cut < max_chars * 0.5:
            cut = window.rfind("\n")
        if cut < max_chars * 0.5:
            cut = window.rfind(". ")
            if cut != -1:
                cut += 1
        if cut < max_chars * 0.5:
            cut = max_chars

        chunks.append(text[:cut].strip())
        text = text[cut:]

    return [c for c in chunks if c]
