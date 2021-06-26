#!/usr/bin/python3
import re
import serial
import time


def _fp_3string(num, scale):
    "rend a number as a fixed-point string of 3 digits"
    return f'{int(num*scale):0>#3}'


class HCS:
    def __init__(self, port=None):
        self.sp = None

        self._model = None
        self._version = None

        if port:
            self.connect(port)

    def connect(self, port):
        self.sp = serial.Serial(port, baudrate=9600, parity='N', bytesize=8, stopbits=1, timeout=0.1, inter_byte_timeout=0.002)
        self.sp.flushInput()
        self.sp.flushOutput()
        if self.model() not in ['HCS-3102', 'HCS-3014', 'HCS-3204'] or self.version() != 'REV3.3':
            raise RuntimeError(f'unsupported model ({self.model()}) or version ({self.version()})')
        # vendor's python lib does this, what other models do we support?
        self.c_factor = 100 if self.model() in ['HCS-3102', 'HCS-3014', 'HCS-3204'] else 10
        return self

    def disconnect(self):
        self.sp.close()
        self.sp = None

    def model(self):
        if not self._model:
            self._model = self.get_model()
        return self._model

    def version(self):
        if not self._version:
            self._version = self.get_version()
        return self._version

    def watch(self, delay=1):
        v_max, c_max = self.get_maximum_voltage_and_current()
        try:
            while True:
                v_target, c_target = self.get_target_voltage_and_current()
                v_display, c_display, mode = self.get_display_voltage_current_and_mode()
                print(time.strftime('%Y-%m-%d %H:%M:%S'),
                      f'mode: {mode}',
                      f'voltage: {v_display:05.2f}V / {v_target:05.2f}V (max {v_max:05.2f}V)',
                      f'current: {c_display:05.2f}A / {c_target:05.2f}A (max {c_max:05.2f}A)')
                time.sleep(delay - time.time() % delay)
        except KeyboardInterrupt:
            print('')

    def _do_transaction(self, cmd, *extras):
        self.sp.write(''.join([cmd]+list(map(str, extras))+['\r']).encode('ascii'))
        response = []
        self.sp.inter_byte_timeout = 0.1    # wait longer for initial response data
        data_read = self.sp.read_until(expected=b'\r')
        self.sp.inter_byte_timeout = 0.002  # expect the rest of the response to come quickly
        while data_read != b'':
            # print(data_read)
            if data_read[-1:] != b'\r':
                raise RuntimeError(f'incomplete response line following {response}: {data_read}')
            response.append(data_read.rstrip().decode('ascii'))
            data_read = self.sp.read_until(expected=b'\r')
        if len(response) not in [1, 2] or response[-1] != 'OK':
            # should never happen
            raise RuntimeError(f'bad response: {response}')
        if response == ['OK']:
            # no-data response
            return True
        return response[0]

    def _do_transaction_no_response(self, cmd, *extras):
        "for set_ commands, raise an exception if response has any data"
        response = self._do_transaction(cmd, *extras)
        if response is not True:
            raise RuntimeError(f'unexpected data in response: {response}')

    def _do_transaction_get_with_regex(self, cmd, regex, cast=int):
        "for get_ commands that return numeric data"
        response = self._do_transaction(cmd)
        match = re.fullmatch(regex, response)
        if not match:
            raise RuntimeError(f'unexpected response {response}')
        results = map(cast, match.groups())
        return results if len(match.groups()) > 1 else next(results)

    def _do_transaction_get_with_dict(self, cmd, responses):
        "for get_ commands that return a status flag"
        response = self._do_transaction(cmd)
        try:
            return responses[response]
        except KeyError:
            raise RuntimeError(f'unexpected response {response}')

    def get_model(self):
        return self._do_transaction('GMOD')

    def get_version(self):
        return self._do_transaction('GVER')

    def get_error_state(self):
        return self._do_transaction_get_with_dict('GERR', {
            '000': False,               # no error
            '001': 'over voltage',
            '002': 'over temperature',
            '003': 'overload'           # bit flag for both over voltage and over temperature?
        })

    def get_maximum_voltage_and_current(self):
        v, c = self._do_transaction_get_with_regex('GMAX', '(\d{3})(\d{3})')
        return v/10, c/self.c_factor

    def set_target_voltage(self, voltage):
        self._do_transaction_no_response('VOLT', _fp_3string(voltage, 10))

    def set_target_current(self, current):
        self._do_transaction_no_response('CURR', _fp_3string(current, self.c_factor))

    def get_target_voltage_and_current(self):
        v, c = self._do_transaction_get_with_regex('GETS', '(\d{3})(\d{3})')
        return v/10, c/self.c_factor

    def get_display_voltage_current_and_mode(self):
        v, c, m = self._do_transaction_get_with_regex('GETD', '(\d{4})(\d{4})([01])')
        # display voltage/current scales are always 100?
        return v/100, c/100, ['CV', 'CC'][m]

    def set_target_voltage_current_and_output_enabled(self, voltage, current, enabled):
        # flag is actually "output power suppressed"
        # so need to flip to the opposite sense
        self._do_transaction_no_response('SEVC', _fp_3string(voltage, 10), _fp_3string(current, self.c_factor), 0 if enabled else 1)

    def get_preset_memories(self):
        # YUCK this command is slow, have to temporarily tune up the serial timeout
        self.sp.timeout = 0.5
        v0, c0, v1, c1, v2, c2 = self._do_transaction_get_with_regex('GETM', '(\d{3})(\d{3})(\d{3})(\d{3})(\d{3})(\d{3})')
        self.sp.timeout = 0.1
        return v0/10, c0/self.c_factor, v1/10, c1/self.c_factor, v2/10, c2/self.c_factor

    def set_preset_memories(self, v0, c0, v1, c1, v2, c2):
        # YUCK this command is slow, have to temporarily tune up the serial timeout
        self.sp.timeout = 0.5
        self._do_transaction_no_response('PROM',
                                         _fp_3string(v0, 10), _fp_3string(c0, self.c_factor),
                                         _fp_3string(v1, 10), _fp_3string(c1, self.c_factor),
                                         _fp_3string(v2, 10), _fp_3string(c2, self.c_factor))
        self.sp.timeout = 0.1

    def run_preset_memory(self, memory):
        self._do_transaction_no_response('RUNM', memory)

    def set_session_state(self, session_state):
        self._do_transaction_no_response({True: 'SESS', False: 'ENDS'}[session_state])

    def get_output_power_enabled(self):
        # flag is actually "output power suppressed"
        # so need to flip to the opposite sense
        return self._do_transaction_get_with_dict('GOUT', {'0': True, '1': False})

    def set_output_power_enabled(self, enabled):
        # flag is actually "output power suppressed"
        # so need to flip to the opposite sense
        self._do_transaction_no_response('SOUT', 0 if enabled else 1)

    def get_over_voltage_limit(self):
        v = self._do_transaction_get_with_regex('GOVP', '(\d{3})')
        return v/10

    def set_over_voltage_limit(self, voltage):
        self._do_transaction_no_response('SOVP', _fp_3string(voltage, 10))

    def get_over_current_limit(self):
        c = self._do_transaction_get_with_regex('GOCP', '(\d{3})')
        return c/self.c_factor

    def set_over_current_limit(self, current):
        self._do_transaction_no_response('SOCP', _fp_3string(current, self.c_factor))


if __name__ == '__main__':
    import sys
    import code
    import readline
    import rlcompleter

    device = '/dev/cu.usbserial-0001'
    hcs = HCS(device)
    repl_locals = {'hcs': hcs}
    for key in {'__name__', '__package__',
                '__loader__', '__spec__',
                '__builtins__', '__file__'}:
        repl_locals[key] = locals()[key]
    ps1 = getattr(sys, "ps1", ">>> ")
    banners = [f'{ps1}hcs = HCS({repr(device)})']
    for banner_cmd in ['hcs.model()', 'hcs.version()',
                       'hcs.get_target_voltage_and_current()',
                       'hcs.get_display_voltage_current_and_mode()',
                       'hcs.get_output_power_enabled()']:
        banners.append(f'{ps1}{banner_cmd}')
        banners.append(repr(eval(banner_cmd)))
    readline.set_completer(rlcompleter.Completer(repl_locals).complete)
    readline.parse_and_bind("tab: complete")
    code.interact(banner='\n'.join(banners), local=repl_locals)
