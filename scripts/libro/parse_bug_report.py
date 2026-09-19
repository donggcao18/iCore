import re
import json

REPORT_FEAT_PATH_SWE = 'scripts/libro/bug_report_parse_results.json'

class BugReportParser:
    def __init__(self, content):
        self.content = content.replace('\t', '    ')
        self.code_blocks = []   # Enclosed in ```
        self.code_examples = [] # 
        self.test_cases = []    #
        self.tracebacks:list[Traceback] = []    # Traceback
        self.error_messages:list[ErrorMessage] = []
        self.how_to_fix = []
        self.other = []
        self.parse()
        
    def parse(self):
        # self.parse_code_blocks()
        self.parse_tracebacks()
        self.parse_error_messages()
    
    def to_json(self):
        return {
            # 'code_blocks': [block.content_lines for block in self.code_blocks],
            'tracebacks': [traceback.to_json() for traceback in self.tracebacks],
            'error_messages': [error.to_json() for error in self.error_messages],
            # 'code_examples': [example.content_lines for example in self.code_examples],
            # 'test_cases': [test_case.content_lines for test_case in self.test_cases],
            # 'how_to_fix': [fix.content_lines for fix in self.how_to_fix],
            # 'other': [other.content_lines for other in self.other]
        }
        
    def parse_code_blocks(self):
        content_lines = self.content.split('\n')
        done_lines = set()
        # Identify content enclosed in ```
        code_block_pattern = re.compile(r"```(.*?)```", re.DOTALL)
        code_blocks = code_block_pattern.findall(self.content)
        for code_block in code_blocks:
            code_block_content = '```' + code_block + '```'
            start_line = content_lines.index(code_block_content.split('\n')[1])
            end_line = start_line + code_block_content.count('\n') - 1
            self.code_blocks.append(CodeBlock(start_line, end_line, content_lines[start_line:end_line]))
            for i in range(start_line, end_line):
                done_lines.add(i)

    
    def parse_code_examples(self):
        pass
    
    def parse_test_cases(self):
        pass
    
    def parse_tracebacks(self):
        tmp_content = self.content
        content_lines = self.content.split('\n')
        
        # 1. Common
        # traceback_pattern = re.compile(
        #     r'((?: *File \".*?\", line \d+, in .+\n+(?:\s*.+\n)?)+)'
        #     r'([\w\.\d]+: .+(?:\n(?!Traceback)[^\n]+)*)',
        #     re.MULTILINE
        # )
        traceback_pattern = re.compile(
            r'((?:(?: *File ".*?", line \d+, in .+\n+(?:\s*(?!File |[\w\.]+:|\[Previous).+\n)?)|(?:\s*\[Previous line repeated \d+ more times\]\n))+)(\s*[\w\.\d]+: .+(?:\n(?!Traceback)[^\n]+)*)',
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
        
        # 2. Missing final exception message
        traceback_pattern = re.compile(
            r'((?:^\s*File \".*\", line \d+, in .*\n(?:\s+.*\n)*)+)',
            re.MULTILINE
        )
        matches = traceback_pattern.findall(tmp_content)
        for i, stack_trace in enumerate(matches, 1):
            start_line = content_lines.index(stack_trace.split('\n')[0])
            end_line = start_line + len(stack_trace.split('\n')) + 1
            tb = Traceback(start_line, end_line, content_lines[start_line:end_line], stack_trace, "")
            self.tracebacks.append(tb)
            
        # Another style
        traceback_pattern = re.compile(
            r'(^\w+:?).*?'  # Capture exception type (e.g., TypeError:)
            r'(?:Traceback \(most recent call last\).*?)\n+'  # Match Traceback title
            r'((?:.*\n)*?)(?=^\w+:?\s)'  # Match traceback code block until a new exception is encountered
            r'(^\w+:?\s*.*\n)',  # Capture exception type and exception message
            re.MULTILINE
        )
        matches = traceback_pattern.findall(tmp_content)
        for i, (exception_type, stack_trace, exception_message) in enumerate(matches, 1):
            start_line = content_lines.index(stack_trace.split('\n')[0])
            end_line = start_line + len(stack_trace.split('\n')) + 1
            tb = Traceback(start_line, end_line, content_lines[start_line:end_line], stack_trace, exception_message)
            self.tracebacks.append(tb)
            self.error_messages.append(tb.error_message)
    
    def parse_error_messages(self):
        def not_in(error_message:str):
            for error in self.error_messages:
                if error.error_message.strip() == error_message.strip():
                    return False
            return True
        # Identify xxError: xx
        error_message_pattern = re.compile(r'(\w+Error: .+?)(?=\n|$)', re.MULTILINE)
        error_messages = error_message_pattern.findall(self.content)
        for error_message in error_messages:
            if not_in(error_message):
                self.error_messages.append(ErrorMessage(error_message))
        # Identify xxException: xx
        exception_message_pattern = re.compile(r'(\w+Exception: .+?)(?=\n|$)', re.MULTILINE)
        exception_messages = exception_message_pattern.findall(self.content)
        for exception_message in exception_messages:
            if not_in(exception_message):
                self.error_messages.append(ErrorMessage(exception_message))
        # Identify xxWarning: xx
        warning_message_pattern = re.compile(r'(\w+Warning: .+?)(?=\n|$)', re.MULTILINE)
        warning_messages = warning_message_pattern.findall(self.content)
        for warning_message in warning_messages:
            if not_in(warning_message):
                self.error_messages.append(ErrorMessage(warning_message))
        
    
    def parse_how_to_fix(self):
        pass
        
class CodeBlock:
    def __init__(self, start_line, end_line, content_lines):
        self.start_line = start_line
        self.end_line = end_line
        self.content_lines = content_lines

class Traceback(CodeBlock):
    def __init__(self, start_line, end_line, content_lines, stack_trace, error_message):
        super().__init__(start_line, end_line, content_lines)
        self.stack_trace = stack_trace
        if error_message:
            self.error_message = ErrorMessage(error_message)
        else:
            self.error_message = None
    
    def to_json(self):  
        return {
            'stack_trace': self.stack_trace,
            'error_message': self.error_message.to_json() if self.error_message else None,
        }

class ErrorMessage():
    def __init__(self, error_message):
        self.error_message = error_message
        self.error_type = self.error_message.split(':')[0]
        self.error_content = ':'.join(self.error_message.split(':')[1:])
        
    def to_json(self):
        return {
            'error_message': self.error_message,
            'error_type': self.error_type,
            'error_content': self.error_content
        }
    

def load_bug_report_features():
    with open(REPORT_FEAT_PATH_SWE, 'r') as f:
        bug_report_features = json.load(f)

    return bug_report_features

def main():
    with open('tdd.txt', 'r') as f:
        swt = f.read()
    instances = swt.split('\n')
    results = load_bug_report_features()
    for id in instances:
        if id == '':
            continue
        if id in results:
            continue
        print(f"Processing {id}...")
        with open(f'data/bug_reports/{id}.txt', 'r') as f:
            content = f.read()
        bug_report = BugReportParser(content)
        results[id] = bug_report.to_json()
        
    # Write to file
    with open(REPORT_FEAT_PATH_SWE, 'w') as f:
        json.dump(results, f, indent=4)
        
if __name__ == "__main__":
    main()