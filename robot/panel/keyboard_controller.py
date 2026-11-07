"""Loopback UI lease and timestamp tickets; no actuator or public remote endpoint."""
import re
import secrets
import threading
import time
from control.input_protocol import uid

class KeyboardError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message

class KeyboardController:
    def __init__(self, service):
        self.service=service
        self.lock=threading.Lock()
        self.tickets={}
        self.lease=None

    @staticmethod
    def validate(payload):
        if type(payload) is not dict:
            raise ValueError('keyboard body')
        if payload=={'action':'open'}:
            return
        fields={'action','control_token','sequence','clock_ticket','elapsed_ms','session_id','keys'}
        if set(payload)!=fields or payload['action'] not in ('arm','keys','release','stop','close'):
            raise ValueError('keyboard action/fields')
        if type(payload['control_token']) is not str or not re.fullmatch(r'[0-9a-f]{32}',payload['control_token']):
            raise ValueError('control token')
        if not uid(payload['sequence']) or not 0<int(payload['sequence'])<=9007199254740991:
            raise ValueError('request sequence')
        ticket=payload['clock_ticket']
        if payload['action']!='close' and (type(ticket) is not str or not re.fullmatch(r'[0-9a-f]{32}',ticket)):
            raise ValueError('clock ticket')
        if type(payload['elapsed_ms']) is not int or not 0<=payload['elapsed_ms']<1000 or not uid(payload['session_id']):
            raise ValueError('sample time/session')
        keys=payload['keys']
        if payload['action']=='keys':
            if type(keys) is not list or len(keys)!=5 or any(type(v) is not bool for v in keys):
                raise ValueError('keys')
        elif keys is not None:
            raise ValueError('unexpected keys')

    @staticmethod
    def current(state):
        value=state['input']['snapshot'] if state['input']['fresh'] else None
        return value if value and value.get('keyboard_channel_open') is True else None

    def prune(self, now):
        self.tickets={k:v for k,v in self.tickets.items() if 0<=now-v[0]<1000}
        if self.lease and now>=self.lease['expires']:
            self.lease=None  # Native250ms timeout remains independent of HTTP.

    def context(self, state):
        now=int(time.monotonic()*1000)
        snap=self.current(state)
        service=state['services']['input']
        generation=service['generation']
        attached=snap['actuator_attached'] if snap else bool(service.get('execution_allowed') and service.get('process_running'))
        execution=snap.get('executor') if snap else service.get('executor') if attached else None
        output=snap['motion_enabled'] if snap else bool(attached and execution and execution.get('output_enabled'))
        result={'keyboard_api_version':1,'available':snap is not None,
            'reason':'仅候选，不连接电机' if snap else '请先在手柄页刷新真实USB输入',
            'clock_ticket':None,'server_clock_ms':now,'generation':generation if snap else None,
            'boot_id':snap['boot_id'] if snap else None,
            'owner':snap['control_owner'] if snap else 'none',
            'session_id':snap['session_id'] if snap else '0',
            'armed':snap['guard_armed'] if snap else False,
            'stop_latched':snap['stop_latched'] if snap else False,
            'neutral_confirmed':snap['neutral_confirmed'] if snap else False,
            'lease_remaining_ms':snap['lease_remaining_ms'] if snap else 0,
            'command_remaining_ms':snap['command_remaining_ms'] if snap else 0,
            'candidate':snap['candidate'] if snap else None,
            'keyboard_sequence':snap.get('keyboard_sequence','0') if snap else '0',
            'keyboard_result':snap.get('keyboard_result','NotArmed') if snap else 'NotArmed',
            'motion_enabled':output,'actuator_attached':attached,
            'native_api_version':3 if attached else 2,'executor':execution,
            'execution_state_current':snap is not None}
        with self.lock:
            self.prune(now)
            if self.lease and (not snap or generation!=self.lease['generation'] or snap['boot_id']!=self.lease['boot']):
                self.lease=None  # Refresh/source loss revokes the old page without a30s wait.
            if snap:
                if len(self.tickets)>=64:
                    oldest=min(self.tickets,key=lambda k:self.tickets[k][0])
                    del self.tickets[oldest]
                ticket=secrets.token_hex(16)
                self.tickets[ticket]=(now,generation,snap['boot_id'])
                result['clock_ticket']=ticket
        return result

    def perform(self, payload, state):
        self.validate(payload)
        now=int(time.monotonic()*1000)
        snap=self.current(state)
        generation=state['services']['input']['generation']
        with self.lock:
            self.prune(now)
            if self.lease and (not snap or generation!=self.lease['generation'] or snap['boot_id']!=self.lease['boot']):
                self.lease=None  # Refresh/source loss revokes the old page without a30s wait.
            action=payload['action']
            if action=='open':
                if not snap:
                    raise KeyboardError(503,'真实输入未就绪')
                if self.lease or snap['control_owner']=='keyboard':
                    raise KeyboardError(409,'已有键盘会话；请释放或等待旧控制权结束')
                token=secrets.token_hex(16)
                self.lease={'token':token,'expires':now+30000,'generation':generation,
                    'boot':snap['boot_id'],'sequence':0,'observed':0}
                return 200,{'status':'opened','control_token':token,
                    'control_expires_in_ms':30000,'generation':generation,'boot_id':snap['boot_id']}
            lease=self.lease
            if not lease or not secrets.compare_digest(payload['control_token'],lease['token']):
                raise KeyboardError(403,'键盘页面会话已失效')
            sequence=int(payload['sequence'])
            if sequence<=lease['sequence']:
                raise KeyboardError(409,'请求序号重复或倒退')
            if action=='close':
                # Revocation is allowed even when its old sample ticket expired.
                # This is a current lifecycle event, never a retimestamped velocity.
                self.lease=None
                if snap and generation==lease['generation'] and snap['boot_id']==lease['boot'] and snap['control_owner']=='keyboard':
                    try:
                        self.service.send_keyboard(generation,lease['boot'],now,'release',snap['session_id'])
                    except (OSError,ValueError,RuntimeError):
                        pass  # Native timeout still expires any prior command.
                return 200,{'status':'closed'}
            if not snap or generation!=lease['generation'] or snap['boot_id']!=lease['boot']:
                self.lease=None
                raise KeyboardError(409,'输入实例已改变；需重新取得会话')
            # Ticket reference lasts1000ms; the derived original sample still expires at250ms.
            ticket=self.tickets.get(payload['clock_ticket'])
            if not ticket or ticket[1:]!=(generation,snap['boot_id']):
                raise KeyboardError(409,'原采样票据已失效')
            observed=ticket[0]+payload['elapsed_ms']
            if not 0<=now-observed<250 or observed<lease['observed']:
                raise KeyboardError(409,'原键盘采样已过期或时钟倒退')
            lease['sequence']=sequence
            lease['observed']=observed
            try:
                result=self.service.send_keyboard(generation,lease['boot'],observed,action,
                    payload['session_id'],tuple(payload['keys']) if action=='keys' else None)
            except (OSError,ValueError,RuntimeError) as exc:
                raise KeyboardError(409,'未写入原生键盘通道：'+type(exc).__name__)
            return 202,result  # Enqueued, not MotionGuard acceptance or physical action.
