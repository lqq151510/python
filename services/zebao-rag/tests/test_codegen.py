import unittest
from unittest.mock import Mock, patch

import httpx
from zebao_rag.codegen import CodeGenerationError, _final_content, generate_code


class CodegenTests(unittest.TestCase):
    @patch("zebao_rag.codegen.httpx.post")
    def test_request_and_response(self, post):
        response = Mock()
        response.json.return_value = {"message": {"content": "<think>internal</think>print(1)"}}
        post.return_value = response
        self.assertEqual(generate_code("  写代码  "), "print(1)")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["messages"][1]["content"], "写代码")
        self.assertFalse(payload["stream"])
        self.assertGreater(post.call_args.kwargs["timeout"], 0)
        self.assertFalse(post.call_args.kwargs["trust_env"])

    @patch("zebao_rag.codegen.httpx.post")
    def test_rejects_invalid_input_without_request(self, post):
        for task in (" ", "a" * 4001):
            with self.assertRaises(CodeGenerationError):
                generate_code(task)
        post.assert_not_called()

    @patch("zebao_rag.codegen.httpx.post", side_effect=httpx.ConnectError("offline"))
    def test_connection_error_is_user_facing(self, post):
        with self.assertRaisesRegex(CodeGenerationError, "Ollama"):
            generate_code("写代码")

    def test_think_tags_in_code_are_preserved(self):
        code = '```python\nprint("</think>")\n```'
        self.assertEqual(_final_content({"content": code}), code)
        self.assertEqual(_final_content({"content": "<think>a</think><think>b</think>" + code}), code)
        self.assertEqual(_final_content({"content": "<think>unfinished"}), "")

    @patch("zebao_rag.codegen.httpx.post", side_effect=httpx.ReadError("private details"))
    def test_request_errors_are_classified(self, post):
        with self.assertRaisesRegex(CodeGenerationError, "Ollama 通信失败") as error:
            generate_code("写代码")
        self.assertNotIn("private details", str(error.exception))


if __name__ == "__main__":
    unittest.main()
