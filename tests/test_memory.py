"""
码搭 CodePilot · 对话记忆测试
"""

import tempfile
import os

from memory import save_turn, load_history, clear_history, get_summary


class TestMemory:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_turn(tmp, "问题1", "回答1")
            save_turn(tmp, "问题2", "回答2")
            msgs = load_history(tmp)
            assert len(msgs) == 4  # 2 Human + 2 AI

    def test_empty_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            msgs = load_history(tmp)
            assert msgs == []

    def test_clear_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_turn(tmp, "test", "answer")
            clear_history(tmp)
            msgs = load_history(tmp)
            assert msgs == []

    def test_get_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_turn(tmp, "问题A", "回答A")
            save_turn(tmp, "问题B", "回答B")
            summary = get_summary(tmp)
            assert len(summary) == 2
            assert "问题A" in summary
            assert "问题B" in summary

    def test_summary_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(25):
                save_turn(tmp, f"问题{i}", f"回答{i}")
            summary = get_summary(tmp, limit=20)
            assert len(summary) == 20

    def test_corrupted_json_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".codepilot", "history.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("not valid json {{{")
            msgs = load_history(tmp)
            assert msgs == []
