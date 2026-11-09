import numpy as np

from karta.decider.laya import cached_embed


def test_cached_embed_only_embeds_new_texts():
    seen = []

    def embed(texts):
        seen.append(list(texts))
        return np.array([[len(t), 1.0] for t in texts])

    fn = cached_embed(embed)
    first = fn(["a", "bb"])
    second = fn(["bb", "ccc", "a"])
    assert seen == [["a", "bb"], ["ccc"]]
    assert first.shape == (2, 2) and second.tolist() == [[2, 1.0], [3, 1.0], [1, 1.0]]
