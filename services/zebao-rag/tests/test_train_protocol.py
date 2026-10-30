"""Training uses inference's text preparation without disabling gradients."""

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from torch import nn

from zebao_rag.worker import TEXT_INSTRUCTION

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "finetune"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("train_lora_protocol_test", SCRIPTS / "train_lora.py")
assert spec and spec.loader
train = importlib.util.module_from_spec(spec)
spec.loader.exec_module(train)


class TinyInput(nn.Module):
    """Tiny deterministic tokenizer and trainable layer, no downloaded weights."""

    def __init__(self, max_length=100):
        super().__init__()
        self.linear = nn.Linear(2, 3, bias=False)
        self.max_seq_length = max_length
        self.seen = []

    def preprocess(self, inputs, prompt=None, **kwargs):
        # Match ST's prefix-before-tokenization; one character is one fake token.
        rendered = [(prompt or "") + text for text in inputs]
        tokenized = [text[: self.max_seq_length] for text in rendered]
        self.seen.append((rendered, tokenized))
        values = [[float(len(text)), float(sum(map(ord, text)) % 97)] for text in tokenized]
        return {"features": torch.tensor(values)}

    def forward(self, features):
        features["sentence_embedding"] = self.linear(features["features"])
        return features


class TrainingProtocolTests(unittest.TestCase):
    def make_model(self, name=None, prompts=None, truncate_dim=None):
        tiny = TinyInput()
        model = SentenceTransformer(
            modules=[tiny],
            device="cpu",
            prompts=prompts,
            default_prompt_name=name,
            truncate_dim=truncate_dim,
        )
        return model, tiny

    def test_query_and_document_follow_actual_encode_with_default_prompt(self):
        model, tiny = self.make_model("document", {"document": "文章："})
        query, document, metadata = train.resolve_training_protocol(model, TEXT_INSTRUCTION)
        self.assertEqual(query, TEXT_INSTRUCTION)
        self.assertEqual(document, "文章：")
        self.assertEqual(metadata["document_prompt_status"], "model_default_applied")
        self.assertEqual(metadata["model_default_prompt_sha256"], hashlib.sha256("文章：".encode()).hexdigest())
        for text, prompt, explicit in [("问题", query, True), ("证据", document, False)]:
            kwargs = {"prompt": TEXT_INSTRUCTION} if explicit else {}
            online = model.encode([text], normalize_embeddings=True, show_progress_bar=False, **kwargs)
            rendered_online = tiny.seen[-1]
            trained = train.embed_training_batch(model, [text], prompt, "cpu")
            self.assertEqual(tiny.seen[-1], rendered_online)
            np.testing.assert_allclose(trained.detach().numpy(), online, rtol=1e-6, atol=1e-6)

    def test_none_and_empty_default_and_invalid_metadata(self):
        model, _ = self.make_model()
        _, prompt, metadata = train.resolve_training_protocol(model, TEXT_INSTRUCTION)
        self.assertIsNone(prompt)
        self.assertEqual(metadata["document_prompt_status"], "none")
        model, _ = self.make_model("empty", {"empty": ""})
        _, prompt, metadata = train.resolve_training_protocol(model, TEXT_INSTRUCTION)
        self.assertEqual(prompt, "")
        self.assertEqual(metadata["model_default_prompt_sha256"], hashlib.sha256(b"").hexdigest())
        model.default_prompt_name = "missing"
        with self.assertRaises(ValueError):
            train.resolve_training_protocol(model, TEXT_INSTRUCTION)
        model.default_prompt_name = "empty"
        model.prompts["empty"] = None
        with self.assertRaises(ValueError):
            train.resolve_training_protocol(model, TEXT_INSTRUCTION)

    def test_prompt_precedes_truncation_and_embedding_truncate_matches(self):
        model, tiny = self.make_model("document", {"document": "DOC:"}, truncate_dim=2)
        old = train.set_training_max_length(model, 6)
        self.assertEqual(old, 100)
        _, document, metadata = train.resolve_training_protocol(model, TEXT_INSTRUCTION)
        self.assertEqual(metadata["embedding_truncate_dim"], 2)
        online = model.encode(["ABCDEFG"], normalize_embeddings=True, show_progress_bar=False)
        self.assertEqual(tiny.seen[-1], (["DOC:ABCDEFG"], ["DOC:AB"]))
        trained = train.embed_training_batch(model, ["ABCDEFG"], document, "cpu")
        self.assertEqual(tiny.seen[-1], (["DOC:ABCDEFG"], ["DOC:AB"]))
        self.assertEqual(trained.shape[1], 2)
        np.testing.assert_allclose(trained.detach().numpy(), online, rtol=1e-6, atol=1e-6)

    def test_ignored_max_length_is_rejected(self):
        class IgnoresLength:
            @property
            def max_seq_length(self):
                return 100

            @max_seq_length.setter
            def max_seq_length(self, value):
                pass

        with self.assertRaises(ValueError):
            train.set_training_max_length(IgnoresLength(), 10)

    def test_real_transformer_peft_forward_updates_lora_weights(self):
        from peft import LoraConfig, get_peft_model
        from sentence_transformers.base.modules import Transformer
        from sentence_transformers.sentence_transformer.modules import Pooling
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import Whitespace
        from transformers import BertConfig, BertModel, PreTrainedTokenizerFast

        torch.manual_seed(19)
        with tempfile.TemporaryDirectory(prefix="devassist-tiny-bert-") as directory:
            model_path = Path(directory)
            config = BertConfig(
                vocab_size=8, hidden_size=16, num_hidden_layers=1,
                num_attention_heads=2, intermediate_size=32,
            )
            BertModel(config).save_pretrained(model_path)
            tokenizer = Tokenizer(WordLevel(
                {"[UNK]": 0, "[PAD]": 1, "[CLS]": 2, "[SEP]": 3,
                 "a": 4, "b": 5, "c": 6, "d": 7},
                unk_token="[UNK]",
            ))
            tokenizer.pre_tokenizer = Whitespace()
            PreTrainedTokenizerFast(
                tokenizer_object=tokenizer, unk_token="[UNK]", pad_token="[PAD]",
                cls_token="[CLS]", sep_token="[SEP]",
            ).save_pretrained(model_path)
            transformer = Transformer(str(model_path), max_seq_length=16)
            model = SentenceTransformer(modules=[transformer, Pooling(16)], device="cpu")
            original = train.get_text_backbone(model)
            adapter = get_peft_model(original, LoraConfig(
                r=2, lora_alpha=4, target_modules=["query", "value"],
                task_type="FEATURE_EXTRACTION",
            ))
            train.attach_text_backbone(model, adapter)
            self.assertIs(model[0].model, adapter)
            self.assertIs(model[0].auto_model, adapter)
            query, _, _ = train.resolve_training_protocol(model, TEXT_INSTRUCTION)
            model.train()
            trainable = [(name, param) for name, param in adapter.named_parameters()
                         if "lora_" in name and param.requires_grad]
            self.assertTrue(trainable)
            before = {name: param.detach().clone() for name, param in trainable}
            optimizer = torch.optim.SGD([param for _, param in trainable], lr=0.1)
            embedding = train.embed_training_batch(model, ["a b c"], query, "cpu")
            weights = torch.arange(embedding.shape[1], dtype=embedding.dtype)
            (embedding * weights).sum().backward()
            self.assertTrue(any(param.grad is not None and param.grad.abs().sum() > 0
                                for _, param in trainable))
            optimizer.step()
            self.assertTrue(any(not torch.equal(before[name], param.detach())
                                for name, param in trainable))

    def test_unsupported_backbone_layout_is_rejected(self):
        class Unsupported:
            auto_model = object()

        with self.assertRaises(ValueError):
            train.get_text_backbone([Unsupported()])

    def test_trainable_weights_receive_gradients_and_update(self):
        torch.manual_seed(7)
        model, tiny = self.make_model("document", {"document": "DOC:"})
        query, _, _ = train.resolve_training_protocol(model, TEXT_INSTRUCTION)
        model.train()
        optimizer = torch.optim.SGD(tiny.parameters(), lr=0.1)
        before = tiny.linear.weight.detach().clone()
        embeddings = train.embed_training_batch(model, ["question"], query, "cpu")
        embeddings.sum().backward()
        self.assertIsNotNone(tiny.linear.weight.grad)
        self.assertGreater(float(tiny.linear.weight.grad.abs().sum()), 0)
        optimizer.step()
        self.assertFalse(torch.equal(before, tiny.linear.weight.detach()))


if __name__ == "__main__":
    unittest.main()
