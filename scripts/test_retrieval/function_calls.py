import os
from scripts.utils import swe_util
import glob

from scripts.generator.make_prompt_util import get_function_content
from scripts.utils.related_test_util import list_all_tests_in_file

def get_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "list_root",
                "description": "List all files and directories inside the root test folder of the project.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                },
                "strict": True
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_folder",
                "description": "List all files and directories inside a given folder. The return result ends with '/' (e.g. 'xxx/yyy/') means it is a folder.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder": {
                            "type": "string",
                            "description": "The path to the folder you want to list."
                        }
                    },
                    "required": ["folder"],
                    "additionalProperties": False
                },
                "strict": True
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_classes_and_functions",
                "description": "List all classes and functions inside a given file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {
                            "type": "string",
                            "description": "The file path to inspect."
                        }
                    },
                    "required": ["file"],
                    "additionalProperties": False
                },
                "strict": True
            }
        },
        {
            "type": "function",
            "function": {
                "name": "read_function",
                "description": "Retrieve the code of a specific function by its name.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {
                            "type": "string",
                            "description": "The file path containing the function you want to retrieve."
                        },
                        "function_name": {
                            "type": "string",
                            "description": "The name of the function whose code you want to retrieve."
                        }
                    },
                    "required": ["file", "function_name"],
                    "additionalProperties": False
                },
                "strict": True
            }
        }
    ]


class FunctionCalls:
    def __init__(self, instance):
        self.bug_id = instance['instance_id']
        self.project = instance['repo']
    
    def get_real_path(self, target_path):
        repo_dir = swe_util.repo_path(self.project)
        path = os.path.join(repo_dir, target_path)
        return path
    
    def list_root(self):
        """List all files and directories inside the root test folder of the project."""
        repo_dir = swe_util.repo_path(self.project)
        test_dir = swe_util.swe_test_path_prefix(self.project, self.bug_id)
        path = os.path.join(repo_dir, test_dir)
        if path.endswith('/'):
            path = path[:-1]
        if '*' not in path:
            path = path + '/*'
        results = glob.glob(path, recursive=True)
        for i, result in enumerate(results):
            results[i] = os.path.relpath(result, repo_dir)
        if self.project == 'sphinx-doc/sphinx':
            results = [file for file in results if file.endswith('.py')]
        return '\n'.join(results)
        
    
    def list_folder(self, folder: str):
        """List all files and directories inside a given folder."""
        repo_dir = swe_util.repo_path(self.project)
        path = self.get_real_path(folder)
        if path.endswith('/'):
            path = path[:-1]
        results = glob.glob(path + '/*', recursive=True)
        if len(results) == 0:
            return f'Error: Path {folder} does not exist'
        for i, result in enumerate(results): 
            if os.path.isdir(result):
                results[i] = os.path.relpath(result, repo_dir) + '/'
            else:
                results[i] = os.path.relpath(result, repo_dir)
        results = [result for result in results if result.endswith('.py') or result.endswith('/')]
        return '\n'.join(results)

    def list_classes_and_functions(self, file):
        """List all classes and functions inside a given file."""
        full_file_path = self.get_real_path(file)
        if not os.path.exists(full_file_path):
            return f'Error: File {file} does not exist'
        tests = list_all_tests_in_file(full_file_path)
        result = [test_name for test_name, test_content in tests.items()]
        if len(result) == 0:
            return f'Error: No **Test** classes or functions found in {file}'
        return '\n'.join(result)

    def read_function(self, file: str, function_name: str) -> str:
        """Retrieve the code of a specific function by its name."""
        path = self.get_real_path(file)
        if not os.path.exists(path):
            return f'Error: File {file} does not exist'
        content = get_function_content(self.project, file, function_name)
        if len(content) == 0:
            return f'Error: Function {function_name} not found in {file}'
        return content

    def call_function(self, function_name, args):
        """Call a function by its name."""
        if function_name == 'list_root':
            return self.list_root()
        elif function_name == 'list_folder':
            return self.list_folder(args['folder'])
        elif function_name == 'list_classes_and_functions':
            return self.list_classes_and_functions(args['file'])
        elif function_name == 'read_function':
            return self.read_function(args['file'], args['function_name'])
        else:
            return f'Error: Unknown function {function_name}'
        
        
if __name__ == '__main__':
    instance = {
        'instance_id': 'django__django-15202',
        'repo': 'django/django',
    }
    fc = FunctionCalls(instance)
    print(fc.call_function('list_root', {}))
    print(fc.call_function('list_folder', {'folder': 'tests/urlpatterns'}))
    # print(fc.call_function('list_folder', {'folder': 'modeling/tests/'}))
    print(fc.call_function('list_classes_and_functions', {'file': 'tests/urlpatterns/test_table.py'}))
    print(fc.call_function('read_function', {'file': 'astropy/io/fits/tests/test_table.py', 'function_name': 'test_ascii_table'}))