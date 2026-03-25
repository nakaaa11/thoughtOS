import voyageai


class Embedder:
    def __init__(self, api_key: str, model: str = "voyage-3"):
        self.client = voyageai.Client(api_key=api_key)
        self.model = model

    def embed(self, text: str) -> list[float]:
        result = self.client.embed([text], model=self.model)
        return result.embeddings[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Voyage AI の最大バッチサイズは128
        all_embeddings = []
        for i in range(0, len(texts), 128):
            batch = texts[i : i + 128]
            result = self.client.embed(batch, model=self.model)
            all_embeddings.extend(result.embeddings)
        return all_embeddings
