"""Static project lifecycle API; never manages vendor ROS/system services."""
def source_row(identifier,title,source):
    value=source.status()
    return {'id':identifier,'title':title,'lifecycle':value['lifecycle'],'running':value['running'],
        'available':value['configured'],'actions':['start','stop'] if value['configured'] else [],'message':value['message']}

class ServiceError(Exception):
    def __init__(self,code,message):self.code,self.message=code,message

def snapshot(server):
    source=server.input_service.status()
    running=source['process_running']
    execution=source.get('executor',{})
    attached=bool(running and source.get('execution_allowed'))
    return {'services_api_version':1,'source':'robot_project_services','instance_id':server.instance,
        'services':[
            {'id':'input','title':'项目输入','lifecycle':source['lifecycle'],'running':running,
             'available':source['refresh_available'],'actions':['start','stop'] if source['refresh_available'] else [],
             'message':source['message']},
            source_row('telemetry','项目遥测采集',server.telemetry),
            source_row('camera','项目相机采集',server.camera),
            {'id':'executor','title':'唯一输出执行器','lifecycle':execution.get('state','disabled') if attached else 'disabled',
             'running':bool(attached and execution.get('output_enabled')),'available':attached,'actions':[],
             'message':'本机短期授权输出模式；网页不能续期，写出成功不等于物理停车' if attached else '默认禁用；需本机短期授权，网页不能开启运动'}],
        'motion_enabled':bool(attached and execution.get('output_enabled')),'actuator_attached':attached}

def perform(server,payload):
    if type(payload) is not dict or set(payload)!={'service','action'} or payload['service'] not in ('input','telemetry','camera') or payload['action'] not in ('start','stop'):
        raise ValueError('service/action')
    if payload['service']!='input':
        source=server.telemetry if payload['service']=='telemetry' else server.camera
        if not source.operation.acquire(False):
            raise ServiceError(409,'项目采集操作进行中')
        try:
            detail=source.stop() if payload['action']=='stop' else source.start('state' if payload['service']=='telemetry' else 'camera')
            return {'status':'completed','service':payload['service'],'action':payload['action'],'detail':detail}
        except (ValueError,OSError,RuntimeError) as exc:
            raise ServiceError(503,'项目采集操作失败：'+type(exc).__name__)
        finally:source.operation.release()
    if not server.input_refresh_lock.acquire(False):
        raise ServiceError(409,'输入服务操作进行中')
    try:
        if payload['action']=='stop':detail=server.input_service.stop()
        else:
            detail=server.input_service.refresh()
            if detail.get('refresh_result')!='started':
                raise ServiceError(503,detail.get('message','输入未启动'))
        return {'status':'completed','service':'input','action':payload['action'],'detail':detail}
    finally:server.input_refresh_lock.release()
