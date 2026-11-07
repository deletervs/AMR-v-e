"""Read-only system facade. Source clocks and lifecycle remain independent."""
import time

TELEMETRY_TTL_MS = 3000
INPUT_TTL_MS = 250


def capability(implemented, available, allowed, reason):
    return {'implemented': implemented, 'available': available,
            'allowed': allowed, 'reason': reason}


def snapshot(server):
    # Collector samples are immutable after publication. Never copy session/power tokens.
    with server.data_lock:
        sample, received = server.data, server.sample_received
    before = server.input_service.status()
    observed_at = time.monotonic()
    observer = server.input_observer.read()
    after = server.input_service.status()
    now = time.monotonic()

    age = (now-received)*1000 if sample is not None else None
    telemetry_fresh = age is not None and 0 <= age < TELEMETRY_TTL_MS
    errors = dict(sample.get('provider_errors', {})) if sample else {}
    telemetry = {'fresh': telemetry_fresh,
        'status': 'fresh' if telemetry_fresh else 'expired' if sample else 'pending',
        'sample_age_ms': age, 'ttl_ms': TELEMETRY_TTL_MS,
        'sampled_elapsed_ms': sample.get('sampled_elapsed_ms') if sample else None,
        'provider_errors': errors, 'data': None}
    if telemetry_fresh:
        telemetry['data'] = {key: sample.get(key) for key in ('system', 'network', 'battery', 'ros')}

    identity = observer.get('instance_id', '').split(':') if observer.get('instance_id') else []
    same_service = (before['generation'] == after['generation']
        and before['pid'] == after['pid'] and after['process_running']
        and before['lifecycle'] == after['lifecycle'] == 'monitoring'
        and before['current_device'] is not None and after['current_device'] is not None
        and before['current_device']['id'] == after['current_device']['id']
        and not after['closed'] and observer.get('phase') == 'panel_input'
        and len(identity) == 3 and identity[1] == str(after['pid']))
    source_age = observer.get('total_age_ms')
    # Include time spent reading/checking rather than renewing source freshness.
    input_age = source_age+(now-observed_at)*1000 if source_age is not None else None
    input_fresh = bool(same_service and observer.get('fresh') is True
        and input_age is not None and 0 <= input_age < INPUT_TTL_MS)
    inputs = {'fresh': input_fresh, 'status': 'fresh' if input_fresh else
        observer['status'] if same_service and observer['status'] != 'fresh' else 'unavailable',
        'source_instance': observer.get('instance_id') if same_service else None,
        'source_age_ms': input_age if same_service else None, 'ttl_ms': INPUT_TTL_MS,
        'valid_for_ms': max(0, INPUT_TTL_MS-input_age) if input_fresh else 0,
        'snapshot': observer['snapshot'] if input_fresh else None,
        'command_usable': False}

    system = telemetry['data']['system'] if telemetry_fresh else None
    resources_available = bool(system and 'system' not in errors
        and any(system.get(k) is not None for k in ('cpu_percent', 'memory_used_percent', 'cpu_temp_c')))
    return {'system_api_version': 1, 'source': 'robot_local_system',
        'instance_id': server.instance, 'os_boot_id': server.os_boot_id,
        'observed_monotonic_ms': int(now*1000), 'command_usable': False,
        'capabilities': {
            'read_system': capability(True, resources_available, True, 'partial resource values may be null'),
            'read_input': capability(True, input_fresh, True, 'current owned input only; no command authority'),
            'refresh_input': capability(True, after['refresh_available'], after['refresh_available'],
                'explicit request only; does not arm or clear stop latch'),
            'exit_panel': capability(True, True, True, 'only this project panel and its owned services'),
            'drive': capability(True, bool(input_fresh and observer['snapshot'].get('motion_enabled')), False,
                'explicit local execution profile only; snapshots never authorize commands'),
            'remote_command': capability(False, False, False, 'unified command arbitration not connected')},
        'services': {
            'panel': {'lifecycle': 'running'},
            'telemetry': {'worker_running': server.collector_thread.is_alive(),
                'data_status': telemetry['status']},
            'input': after},
        'telemetry': telemetry, 'input': inputs,
        'motion_enabled':bool(after.get('execution_allowed') and after.get('process_running') and after.get('executor',{}).get('output_enabled')),
        'actuator_attached':bool(after.get('execution_allowed') and after.get('process_running')),
        'execution_state_current':input_fresh}
