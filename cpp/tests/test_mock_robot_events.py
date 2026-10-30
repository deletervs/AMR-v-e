"""Check the real C++ event stream, JSON escaping and bounded history (Python 3.8+)."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import unittest


def reject_constant(value):
    raise ValueError("Invalid JSON constant: " + value)


def produce(*arguments):
    result = subprocess.run([str(PROGRAM)] + list(arguments),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace"))
    return result.stdout, [json.loads(line, parse_constant=reject_constant)
                           for line in result.stdout.decode("utf-8").splitlines()]


class EventOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario_raw, cls.scenario = produce()
        cls.stream_raw, cls.stream = produce("--stream-test")
        cls.capacity_raw, cls.capacity = produce("--event-log-test")

    def assert_history(self, frame):
        self.assertEqual(frame["source"], "mock")
        self.assertIs(frame["motion_enabled"], False)
        history = frame["event_log"]
        self.assertEqual(set(history), {"entries", "truncated"})
        self.assertIs(type(history["truncated"]), bool)
        self.assertLessEqual(len(history["entries"]), 100)
        previous = -1
        for entry in history["entries"]:
            self.assertEqual(set(entry), {"elapsed_ms", "level", "source", "message"})
            self.assertIs(type(entry["elapsed_ms"]), int)
            self.assertGreaterEqual(entry["elapsed_ms"], max(0, previous))
            self.assertLessEqual(entry["elapsed_ms"], frame["sim_time_ms"])
            self.assertIn(entry["level"], ("Info", "Warning", "Error"))
            self.assertTrue(1 <= len(entry["source"].encode("utf-8")) <= 64)
            self.assertTrue(1 <= len(entry["message"].encode("utf-8")) <= 512)
            previous = entry["elapsed_ms"]

    def test_four_scenarios_share_history(self):
        self.assertEqual(len(self.scenario), 4)
        self.assertEqual([len(frame["event_log"]["entries"]) for frame in self.scenario],
                         [1, 5, 8, 11])
        for frame in self.scenario:
            self.assert_history(frame)
            self.assertFalse(frame["event_log"]["truncated"])
        self.assertEqual(self.scenario[0]["event_log"]["entries"],
                         [{"elapsed_ms": 0, "level": "Info", "source": "mock",
                           "message": "模拟状态发布启动"}])
        for before, after in zip(self.scenario, self.scenario[1:]):
            self.assertEqual(after["event_log"]["entries"][:len(before["event_log"]["entries"])],
                             before["event_log"]["entries"])

    def test_only_transitions_generate_events(self):
        counts = [1, 1, 5, 5, 8, 8, 9, 10, 11, 11, 11, 11, 15, 15, 19, 19, 22]
        self.assertEqual(len(self.stream), 17)
        self.assertEqual([len(frame["event_log"]["entries"]) for frame in self.stream], counts)
        for frame in self.stream:
            self.assert_history(frame)
        self.assertEqual(self.stream[8]["event_log"], self.stream[11]["event_log"],
                         "Value-only changes must not repeatedly log the same quality")
        self.assertEqual(self.stream[0]["event_log"], self.stream[1]["event_log"])

    def test_independent_quality_events_have_correct_level_and_time(self):
        stale = self.stream[4]["event_log"]["entries"][-3:]
        self.assertEqual([event["source"] for event in stale],
                         ["battery", "odometry", "system_resources"])
        self.assertTrue(all(event["elapsed_ms"] == 2000 and event["level"] == "Warning"
                            and event["message"] == "Valid -> Stale" for event in stale))
        for index, source, time_ms in ((6, "odometry", 3000),
                                       (7, "system_resources", 3500),
                                       (8, "battery", 4000)):
            self.assertEqual(self.stream[index]["event_log"]["entries"][-1],
                             {"elapsed_ms": time_ms, "level": "Info", "source": source,
                              "message": "Stale -> Valid"})

    def test_simulated_reset_preserves_old_history(self):
        before = self.stream[11]["event_log"]["entries"]
        reset = self.stream[12]["event_log"]["entries"]
        self.assertEqual(reset[:len(before)], before)
        self.assertEqual(len(reset) - len(before), 4)
        self.assertTrue(all(event["elapsed_ms"] == 6000 for event in reset[len(before):]))
        self.assertEqual(self.stream[12]["sim_time_ms"], 6000)
        self.assertFalse(self.stream[12]["connected"])
        self.assertEqual(reset[0]["elapsed_ms"], 0)

    def test_a_new_process_starts_with_fresh_history(self):
        _, second = produce("--stream-test")
        self.assertEqual(second[0]["event_log"], self.stream[0]["event_log"])
        self.assertEqual(len(second[0]["event_log"]["entries"]), 1)

    def test_capacity_control_text_and_utf8_round_trip(self):
        self.assertEqual(len(self.capacity_raw.splitlines()), 1,
                         "Escaped newline/control bytes must not split an NDJSON record")
        self.assertGreater(len(self.capacity_raw), 65536)
        self.assertLess(len(self.capacity_raw), 512 * 1024)
        self.assertEqual(len(self.capacity), 1)
        frame = self.capacity[0]
        self.assert_history(frame)
        history = frame["event_log"]
        self.assertIs(history["truncated"], True)
        self.assertEqual(len(history["entries"]), 100)
        self.assertEqual([entry["elapsed_ms"] for entry in history["entries"]], list(range(1, 101)))
        source = 'mock"\\\n来源\0'
        message = "\0" * 400 + '中文"\\\n\t\r\b\f' + "".join(chr(value) for value in range(1, 32))
        levels = ("Info", "Warning", "Error")
        for index, entry in enumerate(history["entries"], 1):
            self.assertEqual(entry["source"], source)
            self.assertEqual(entry["message"], message + str(index))
            self.assertEqual(entry["level"], levels[index % 3])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("program", type=Path)
    options, remaining = parser.parse_known_args()
    PROGRAM = options.program.resolve()
    unittest.main(argv=[sys.argv[0]] + remaining)
