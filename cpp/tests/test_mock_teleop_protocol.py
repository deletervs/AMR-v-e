"""Exercise the real mock controller executable, including receipt-time expiry."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest

EXECUTABLE = str(Path(sys.argv.pop(1)).resolve())

class Agent:
    def __init__(self):
        self.proc = subprocess.Popen([EXECUTABLE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="ascii")
        self.lines = queue.Queue()
        def reader():
            for line in self.proc.stdout:
                self.lines.put(line)
        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()
    def ask(self, command):
        self.proc.stdin.write(command+"\n")
        self.proc.stdin.flush()
        return json.loads(self.lines.get(timeout=4))
    def close(self):
        try:
            if self.proc.poll() is None:
                self.ask("quit")
                self.proc.stdin.close()
                self.proc.wait(timeout=4)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=4)
            self.proc.stdout.close()
            self.proc.stderr.close()
            self.reader.join(timeout=1)
    def drive(self, state, seq, deadman=False, forward=False, command="keys", tail=None,
              issued=None, expires=None):
        issued = state["elapsed_ms"] if issued is None else issued
        expires = issued+250 if expires is None else expires
        args = [command,state["boot_id"],state["session_id"],state["controller_id"],str(seq),
                str(issued),str(expires),str(int(deadman))]
        args.extend(tail if tail is not None else [str(int(forward)),"0","0","0"])
        return self.ask(" ".join(args))

class ProtocolTests(unittest.TestCase):
    def setUp(self): self.agent=Agent()
    def tearDown(self): self.agent.close()
    def neutral(self, owner="keyboard"):
        state=self.agent.ask("arm 42 "+owner)
        self.assertEqual(state["result"],"Armed")
        if owner=="keyboard": return self.agent.drive(state,1)
        return self.agent.drive(state,1,command="pad",tail=["0","0"])
    def assertZero(self,s):
        self.assertEqual((s["linear_mps"],s["angular_radps"]),(0,0))
    def test_starts_disarmed_ids_are_strings(self):
        s=self.agent.ask("tick");self.assertEqual(s["source"],"mock_teleop")
        self.assertZero(s);self.assertFalse(s["armed"])
        for field in ["boot_id","session_id","controller_id","sequence"]:
            self.assertIsInstance(s[field],str)
        self.assertNotEqual(s["boot_id"],"0")
    def test_neutral_then_direction_then_release(self):
        s=self.agent.ask("arm 42 keyboard")
        s=self.agent.drive(s,1,True,True)
        self.assertEqual(s["result"],"NeutralRequired");self.assertZero(s)
        s=self.agent.drive(s,1);self.assertTrue(s["neutral_confirmed"])
        s=self.agent.drive(s,2,True,True)
        self.assertEqual(s["linear_mps"],.25)
        s=self.agent.drive(s,3,False,True);self.assertZero(s)
    def test_raw_input_must_be_neutral_even_when_deadman_released(self):
        s=self.agent.ask("arm 42 keyboard")
        s=self.agent.drive(s,1,False,True)
        self.assertEqual(s["result"],"NeutralRequired");self.assertFalse(s["neutral_confirmed"])
        self.agent.ask("release 42 "+s["boot_id"]+" "+s["session_id"])
        s=self.agent.ask("arm 42 gamepad")
        s=self.agent.drive(s,1,False,command="pad",tail=["0","-1"])
        self.assertEqual(s["result"],"NeutralRequired");self.assertFalse(s["neutral_confirmed"])
    def test_gamepad_direction_and_release(self):
        s=self.neutral("gamepad")
        s=self.agent.drive(s,2,True,command="pad",tail=["-1","-1"])
        self.assertEqual((s["linear_mps"],s["angular_radps"]),(.25,.6))
        s=self.agent.drive(s,3,False,command="pad",tail=["-1","-1"]);self.assertZero(s)
    def test_duplicate_and_other_client_cannot_take_lease(self):
        s=self.neutral();s=self.agent.drive(s,2,True,True)
        self.assertEqual(self.agent.ask("arm 43 gamepad")["result"],"Busy")
        self.assertEqual(self.agent.drive(s,2,True,True)["result"],"InvalidSequence")
        self.assertEqual(self.agent.ask("release 43 "+s["boot_id"]+" "+s["session_id"])["result"],"InvalidSession")
    def test_software_stop_requires_clear_and_new_arm(self):
        s=self.neutral();s=self.agent.drive(s,2,True,True)
        stopped=self.agent.ask("stop");self.assertZero(stopped);self.assertTrue(stopped["stop_latched"])
        self.assertEqual(self.agent.ask("arm 42 keyboard")["result"],"StopLatched")
        cleared=self.agent.ask("clear");self.assertZero(cleared);self.assertFalse(cleared["armed"])
        new=self.agent.ask("arm 42 keyboard");self.assertNotEqual(new["session_id"],s["session_id"])
        self.assertEqual(self.agent.drive(s,3,True,True)["result"],"InvalidSession")
    def test_expired_packet_does_not_get_receipt_time_ttl(self):
        s=self.neutral();issued=s["elapsed_ms"];time.sleep(.09)
        late=self.agent.drive(s,2,True,True,issued=issued,expires=issued+40)
        self.assertEqual(late["result"],"InvalidWindow");self.assertZero(late)
    def test_idle_timeout_revokes_control(self):
        s=self.neutral();self.agent.drive(s,2,True,True);time.sleep(.31)
        expired=self.agent.ask("tick");self.assertZero(expired);self.assertFalse(expired["armed"])
        self.assertEqual(expired["stop_reason"],"CommandExpired")
    def test_link_recovery_never_auto_arms(self):
        s=self.neutral();self.agent.drive(s,2,True,True)
        self.assertZero(self.agent.ask("link 0"));s=self.agent.ask("link 1")
        self.assertZero(s);self.assertFalse(s["armed"])
    def test_invalid_protocol_is_zero_and_latched(self):
        self.neutral()
        for command in ["arm -1 keyboard","arm 18446744073709551616 keyboard","link 2","tick unexpected"]:
            s=self.agent.ask(command);self.assertEqual(s["result"],"InvalidInput")
            self.assertZero(s);self.assertTrue(s["stop_latched"])
    def test_agent_restarts_change_boot(self):
        old=self.agent.ask("tick")["boot_id"]
        other=Agent()
        try: self.assertNotEqual(other.ask("tick")["boot_id"],old)
        finally: other.close()
    def test_no_runtime_robot_option(self):
        p=subprocess.run([EXECUTABLE,"--robot"],capture_output=True,timeout=4)
        self.assertEqual(p.returncode,2)

if __name__=="__main__": unittest.main()
