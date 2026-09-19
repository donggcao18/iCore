import json

from scripts.libro.parse_bug_report import BugReportParser, ErrorMessage, Traceback
import re


def parse_buggy_output(proj, buggy_output, exception_type=None, value_matching=None, exception_msg=None, mode='swe'):
    """
    Parse failure output (mode: d4j, ghrb)
    """
    if mode == 'swe': 
        return parse_buggy_output_swe(buggy_output)
    else:
        raise NotImplementedError


def parse_buggy_output_swe(buggy_output):
    # buggy_output_lines = buggy_output.strip().split('\n')
    parser = BuggyOutputParser(buggy_output)
    tracebacks = [tb.to_json() for tb in parser.tracebacks]
    if len(parser.error_messages) == 0:
        # Match by line
        content_lines = buggy_output.split('\n')
        line = content_lines[-1]
        if line.startswith('E'):
            error_type = line.split(': ')[-1]
            exceptions = [{
                "error_type": error_type,
                "error_message": '',
                "error_content": None
            }]
        else:
            exceptions = [{
                "error_type": 'UnknownError',
                "error_message": '',
                "error_content": None
            }]
    else:
        exceptions = [e.to_json() for e in parser.error_messages]
    return {
        'exceptions': exceptions,
        'tracebacks': tracebacks,
        'full_output': buggy_output
    }

def clean_output_value(s):
    return s.replace('[', '').replace(']', '').replace('...', ' ')

class BuggyOutputParser:
    def __init__(self, buggy_output):
        self.buggy_output = buggy_output
        self.tracebacks: list[Traceback] = []
        self.error_messages: list[ErrorMessage] = []
        self.parse_buggy_output()
    
    def parse_buggy_output(self):
        content_lines = self.buggy_output.split('\n')
        if len(content_lines) > 1000:
            # Abandon regex matching, likely a RecursionError
            if 'RecursionError' not in self.buggy_output:
                print("Not a RecursionError.")
                self.error_messages.append(ErrorMessage('Output too long, unable to parse'))
            else:
                self.error_messages.append(ErrorMessage('RecursionError: maximum recursion depth exceeded'))
            return
        
        new_lines = []
        for line in content_lines:
            if '[Previous line repeated' in line:
                continue
            new_lines.append(line)

        self.buggy_output = '\n'.join(new_lines)
                
        # Identify stack information
        self.parse_traceback()
        # Identify exception information
        self.parse_error_messages()
        
    def parse_traceback(self):
        tmp_content = self.buggy_output
        content_lines = self.buggy_output.split('\n')
        
        # 1. Common
        traceback_pattern = re.compile(
            r'('
                r'(?:'
                    r'^ *File \".*?\", line \d+(?:, in .*)?\n'  # Match File line
                    r'(?:^ +.*\n)*'   # Match code line/pointer line (must start with space to prevent consuming Exception)
                r')+'
            r')'
            r'('
                r'^[^\s].+'           # Exception information (usually flush left, not starting with space)
                r'(?::\s+.+)?'        # Optional colon and details
                r'(?:\n(?!Traceback| *File ).+)*' # Subsequent lines of exception information (no new Traceback or File)
            r')',
            re.MULTILINE
        )
        matches = traceback_pattern.findall(tmp_content)
        for i, (stack_trace, exception_message) in enumerate(matches, 1):
            start_line = content_lines.index(stack_trace.split('\n')[0])
            end_line = start_line + len(stack_trace.split('\n')) + 1
            tb = Traceback(start_line, end_line, content_lines[start_line:end_line], stack_trace, exception_message)
            self.tracebacks.append(tb)
            self.error_messages.append(tb.error_message)
            
            tmp_content = tmp_content.replace(stack_trace+exception_message, '<traceback>')
            # tmp_content = tmp_content.replace(exception_message, '<exception_message>')
        
        traceback_pattern = re.compile(
            r'((?:^ *File \".*?\", line \d+, in .+\n+(?:\s*.+\n)?)+)'
            r'([\w\.\d]+(?:\n(?!Traceback)[^\n]+)*)',
            re.MULTILINE
        )
        matches = traceback_pattern.findall(tmp_content)
        for i, (stack_trace, exception_message) in enumerate(matches, 1):
            start_line = content_lines.index(stack_trace.split('\n')[0])
            end_line = start_line + len(stack_trace.split('\n')) + 1
            tb = Traceback(start_line, end_line, content_lines[start_line:end_line], stack_trace, exception_message)
            self.tracebacks.append(tb)
            self.error_messages.append(tb.error_message)
            
            tmp_content = tmp_content.replace(stack_trace+exception_message, '<traceback>')
        
        # Another style: sklearn
        traceback_pattern = re.compile(
            r'((?:[^\n]*:\d+: in .*\n(?:\s+.+\n)*)+)'
            r'(?:.*\n)*'
            r'(E\s+.*(?:\nE\s+.*)*)'
        )
        matches = traceback_pattern.findall(tmp_content)
        for i, (stack_trace, exception_message) in enumerate(matches, 1):
            start_line = content_lines.index(stack_trace.split('\n')[0])
            end_line = start_line + len(stack_trace.split('\n')) + 1
            tb = Traceback(start_line, end_line, content_lines[start_line:end_line], stack_trace, exception_message)
            self.tracebacks.append(tb)
            self.error_messages.append(tb.error_message)
            tmp_content = tmp_content.replace(stack_trace+exception_message, '<traceback>')
        # sympy Error has no message
        traceback_pattern = re.compile(
            r'((?:^E? *File \".*?\", line \d+, in .+\n+'
            r'(?:\s*.+\n)?)+)([\w\.\d]+)'
        )
        matches = traceback_pattern.findall(tmp_content)
        for i, (stack_trace, exception_message) in enumerate(matches, 1):
            target_line = stack_trace.split('\n')[0]
            start_line = content_lines.index(target_line)
            end_line = start_line + len(stack_trace.split('\n')) + 1
            tb = Traceback(start_line, end_line, content_lines[start_line:end_line], stack_trace, exception_message)
            self.tracebacks.append(tb)
            self.error_messages.append(tb.error_message)
            tmp_content = tmp_content.replace(stack_trace+exception_message, '<traceback>')
        # pytest doesn't seem to match traceback, only error message
    
    def parse_error_messages(self):
        def not_in(error_message:str):
            for error in self.error_messages:
                if error.error_message.strip() == error_message.strip():
                    return False
            return True
        # Identify xxError: xx
        error_message_pattern = re.compile(r'(\w+Error: .+?)(?=\n|$)', re.MULTILINE)
        error_messages = error_message_pattern.findall(self.buggy_output)
        for error_message in error_messages:
            if not_in(error_message):
                self.error_messages.append(ErrorMessage(error_message))
        # Identify xxException: xx
        exception_message_pattern = re.compile(r'(\w+Exception: .+?)(?=\n|$)', re.MULTILINE)
        exception_messages = exception_message_pattern.findall(self.buggy_output)
        for exception_message in exception_messages:
            if not_in(exception_message):
                self.error_messages.append(ErrorMessage(exception_message))
        # Identify xxWarning: xx
        warning_message_pattern = re.compile(r'(\w+Warning: .+?)(?=\n|$)', re.MULTILINE)
        warning_messages = warning_message_pattern.findall(self.buggy_output)
        for warning_message in warning_messages:
            if not_in(warning_message):
                self.error_messages.append(ErrorMessage(warning_message))
        # Failed: xx
        failed_message_pattern = re.compile(r'(^E\s*Failed: .+?)(?=\n|$)', re.MULTILINE)
        failed_messages = failed_message_pattern.findall(self.buggy_output)
        for failed_message in failed_messages:
            if not_in(failed_message):
                self.error_messages.append(ErrorMessage(failed_message))
        # xx: Failed
        failed_message_pattern = re.compile(r'(^.+:\s*Failed)(?=\n|$)', re.MULTILINE)
        failed_messages = failed_message_pattern.findall(self.buggy_output)
        for failed_message in failed_messages:
            if not_in(failed_message):
                self.error_messages.append(ErrorMessage('Failed:'))

def get_test_output(instance_id, dataset):
    with open(f'results/{dataset}/deepseek-chat.json', 'r') as f:
        test_outputs = json.load(f)
    return test_outputs[instance_id]

def main():
    with open('swt.txt', 'r') as f:
        swt = f.read()
    instances = swt.split('\n')
    results = {}
    dataset = 'related_test'
    for id in instances:
        if id == '':
            continue
        print(f"Processing {id}...")
        test_outputs = get_test_output(id, dataset)
        results[id] = {}
        for file, test_result in test_outputs.items():
            if isinstance(test_result, str):
                continue
            else:
                buggy_output = test_result['buggy']['fib_error_msg']
                if len(buggy_output) == 0:
                    continue
                buggy_output = '\n'.join(buggy_output)
            parsed_buggy_output = parse_buggy_output_swe(buggy_output)
            results[id][file] = parsed_buggy_output
        
    with open(f'results/{dataset}/output_parse_results.json', 'w') as f:
        json.dump(results, f, indent=4)
        
if __name__ == "__main__":
    main()