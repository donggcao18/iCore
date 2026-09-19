import os
import re
from datetime import datetime

from scripts.config import ENV_NAME_TEMPLATE, REPO_ROOT_DIR
from scripts.env_setup.constants import MAP_VERSION_TO_INSTALL

def repo_path(proj):
    proj_name = proj.split('/')[-1]
    return os.path.expanduser(os.path.join(REPO_ROOT_DIR, f'{proj_name}/'))

class TimeoutException(Exception):
    pass

def swe_test_cmd(proj, version, test_name):
    if '/' in proj:
        proj = proj.split('/')[1]
    if proj in {'astropy', 'matplotlib', 'flask', 'xarray', 'pylint', 'scikit-learn', 'sphinx', 'requests'}:
        test_name = test_name.split('::')
        test_name[-1] = test_name[-1].replace('.', '::')
        test_name = '::'.join(test_name)
        return f'python -m pytest --no-header --tb=short --show-capture=no --disable-warnings -p no:cacheprovider {test_name}'
    elif proj == 'seaborn':
        test_name = test_name.split('::')
        test_name[-1] = test_name[-1].replace('.', '::')
        test_name = '::'.join(test_name)
        return f'pytest --no-header --show-capture=no --disable-warnings {test_name}'
    elif proj == 'pytest':
        test_name = test_name.split('::')
        test_name[-1] = test_name[-1].replace('.', '::')
        test_name = '::'.join(test_name)
        return f'pytest --disable-warnings --show-capture=no {test_name} -v'
    elif proj == 'django':
        test_name = test_name.replace('.py', '').replace('/', '.').replace('::', '.')
        if test_name.startswith('tests'):
            test_name = test_name.removeprefix('tests.')
        return f'./tests/runtests.py --settings=test_sqlite {test_name}'
    # elif proj == 'requests':
        # if version in {'2.3', '2.4', '2.7'}:
        #     test_name = test_name.replace('test_requests.py::', '')
        #     return f'./test_requests.py {test_name}'
        # elif version == '2.10':
        #     return f'py.test {test_name}'
        # elif version == '0.14':
        #     test_name = test_name.replace('::', ':')
        #     return f'nosetests {test_name}'
    elif proj == 'sympy':
        assert len(test_name.split('::')) == 2, "sympy test name?"
        test_path = test_name.split('::')[0]
        test_name = test_name.split('::')[1]
        return f'PYTHONWARNINGS=\'ignore::UserWarning,ignore::SyntaxWarning\' bin/test -C {test_path} -k {test_name}'
    else:
        raise ValueError(f'Unrecognized project {proj}')


def swe_setup_cmd(proj, version):
    install = MAP_VERSION_TO_INSTALL[proj][version]
    cmd = [install['install']]
    if 'pre_install' in install:
        cmd = install['pre_install'] + cmd
    if 'eval_commands' in install:
        cmd = cmd + install['eval_commands']
    return ' && '.join(cmd)


def swe_path_prefix(proj):
    if proj == 'astropy/astropy':
        return 'astropy'
    elif proj == 'django/django':
        return 'django'
    elif proj == 'matplotlib/matplotlib':
        return 'lib/matplotlib'
    elif proj == 'mwaskom/seaborn':
        return 'seaborn'
    elif proj == 'pallets/flask':
        return 'src/flask'
    elif proj == 'psf/requests':
        return 'src/requests'
    elif proj == 'pydata/xarray':
        return 'xarray'
    elif proj == 'pylint-dev/pylint':
        return 'pylint'
    elif proj == 'pytest-dev/pytest':
        return 'src/_pytest'
    elif proj == 'scikit-learn/scikit-learn':
        return 'sklearn'
    elif proj == 'sphinx-doc/sphinx':
        return 'sphinx'
    elif proj == 'sympy/sympy':
        return 'sympy'
    else:
        raise ValueError(f'Unrecognized project {proj}')

def swe_test_path_prefix(proj, bug_id):
    if proj == 'astropy/astropy':
        return 'astropy/**/tests/'
    elif proj == 'django/django':
        return 'tests/'
    elif proj == 'matplotlib/matplotlib':
        return 'lib/**/tests/'
    elif proj == 'mwaskom/seaborn':
        return 'tests/'
    elif proj == 'pallets/flask':
        return 'tests/'
    elif proj == 'psf/requests':
        # if bug_id in {'psf__requests-1963', 'psf__requests-2148', 'psf__requests-2317', 'psf__requests-2674', 'psf__requests-1142', 'psf__requests-1766'}:
        return ''
        # return 'tests/'
    elif proj == 'pydata/xarray':
        return 'xarray/tests/'
    elif proj == 'pylint-dev/pylint':
        return 'tests/'
    elif proj == 'pytest-dev/pytest':
        return 'testing/'
    elif proj == 'scikit-learn/scikit-learn':
        return 'sklearn/**/tests/'
    elif proj == 'sphinx-doc/sphinx':
        return 'tests/'
    elif proj == 'sympy/sympy':
        return 'sympy/**/tests/'
    else:
        raise ValueError(f'Cannot find test path prefix for {proj}')
        
def parse_abs_path(jfile):
    repo_dir_name = jfile.removeprefix(REPO_ROOT_DIR).split('/')[0]
    repo_dir_path = REPO_ROOT_DIR + repo_dir_name + '/'
    rel_jfile_path = jfile.removeprefix(repo_dir_path)
    return repo_dir_path, rel_jfile_path

def log(*args):
    '''Used only when flush is desired'''
    now = datetime.now()
    now_str = now.strftime(r'%Y-%m-%d %H:%M:%S.%f')
    print(f'[{now_str}]', *args, flush=True)
    
def get_env_name(bug_report):
    proj = bug_report['repo']
    version = bug_report['version']
    return ENV_NAME_TEMPLATE.format(name1=proj.split('/')[0], name2=proj.split('/')[1], version=version)

def get_env_path(bug_report):
    env_name = get_env_name(bug_report)
    return f'~/miniconda3/envs/{env_name}/bin/python'

def instance_id_to_proj(instance_id):
    proj = re.sub(r'-\d+$', '', instance_id)
    proj = proj.replace('__', '/')
    return proj