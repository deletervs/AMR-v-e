"""Bounded in-memory command evidence, never motor feedback or authorization."""
from collections import deque, OrderedDict
import threading
import time

class CommandHistory:
    capacity=200
    def __init__(self):
        self.lock=threading.Lock()
        self.started=time.monotonic()
        self.events=deque(maxlen=self.capacity)
        self.pending=OrderedDict()
        self.counter=0
        self.dropped=0
        self.receipt_epoch=None
        self.last_receipt=0
        self.last_guard=None

    def append(self, stage, generation, boot, sequence, action, result=None, native_elapsed=None):
        self.counter+=1
        if len(self.events)==self.capacity:
            self.dropped+=1
        self.events.append({'id':self.counter,'elapsed_ms':int((time.monotonic()-self.started)*1000),
            'stage':stage,'generation':generation,'boot_id':boot,'sequence':sequence,
            'action':action,'result':result,'native_elapsed_ms':native_elapsed})

    def expire(self):
        now=time.monotonic()
        for key,(action,created) in list(self.pending.items()):
            if now-created>=.5:
                self.append('unconfirmed',key[0],key[1],key[2],action,'receipt_timeout')
                del self.pending[key]

    def enqueued(self, generation, boot, sequence, action):
        with self.lock:
            self.expire()
            key=(generation,boot,str(sequence))
            if len(self.pending)>=64:
                old,(old_action,_) = self.pending.popitem(last=False)
                self.append('unconfirmed',old[0],old[1],old[2],old_action,'pending_capacity')
            self.pending[key]=(action,time.monotonic())
            self.append('enqueued',generation,boot,str(sequence),action)

    def observe(self, generation, frame):
        with self.lock:
            boot=frame['boot_id']
            epoch=(generation,boot)
            if epoch!=self.receipt_epoch:
                self.finish_locked('source_changed',epoch)
                self.receipt_epoch=epoch
                self.last_receipt=0
                self.last_guard=None
            for receipt in frame.get('keyboard_receipts',[]):
                number=int(receipt['id'])
                if number<=self.last_receipt:
                    continue
                if number>self.last_receipt+1:
                    self.append('receipt_gap',generation,boot,None,'receipt',
                        'missing_'+str(number-self.last_receipt-1),receipt['elapsed_ms'])
                self.last_receipt=number
                sequence=receipt['request_sequence']
                pending=self.pending.pop((generation,boot,sequence),None)
                # Never pair a newer sequence with an older request by inequality.
                self.append('native_result',generation,boot,sequence,
                    pending[0] if pending else 'unmatched',receipt['result'],receipt['elapsed_ms'])
            current=(frame.get('control_owner','unknown'),frame['session_id'],
                frame['stop_latched'],frame['stop_reason'])
            if current!=self.last_guard:
                self.append('guard_state',generation,boot,None,current[0],
                    frame['stop_reason'],frame['elapsed_ms'])
                self.last_guard=current
            self.expire()

    def finish_locked(self, reason, keep_epoch=None):
        for key,(action,_) in list(self.pending.items()):
            if keep_epoch is not None and key[:2]==keep_epoch:
                continue
            self.append('unconfirmed',key[0],key[1],key[2],action,reason)
            del self.pending[key]

    def finish(self, reason):
        with self.lock:
            self.finish_locked(reason)

    def snapshot(self):
        with self.lock:
            self.expire()
            return {'history_api_version':1,'source':'robot_control_history','capacity':self.capacity,
                'total_retained':len(self.events),'dropped':self.dropped,'returned_limit':20,
                'events':[dict(v) for v in list(self.events)[-20:]],'command_usable':False}
