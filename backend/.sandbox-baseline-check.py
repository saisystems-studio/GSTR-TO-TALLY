import os, sys, importlib.abc, importlib.util, subprocess
os.environ['DJANGO_SETTINGS_MODULE']='config.test_settings'
modules={}
for path in ['gst_tally/services/gst_lookup/service.py', 'gst_tally/services/gst_lookup/providers/sandbox.py', 'gst_tally/services/party_lookup.py', 'superadmin/services/sandbox_configuration.py', 'gst_tally/tests/test_sandbox_provider.py', 'gst_tally/tests/test_party_bulk_lookup.py']:
    name=path[:-3].replace('/', '.')
    modules[name]=subprocess.check_output(['git','show','HEAD:backend/'+path],text=True,encoding='utf-8')
class Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        return importlib.util.spec_from_loader(fullname,self) if fullname in modules else None
    def create_module(self,spec): return None
    def exec_module(self,module): exec(compile(modules[module.__name__],module.__name__,'exec'),module.__dict__)
sys.meta_path.insert(0,Loader())
from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py','test','gst_tally.tests.test_party_bulk_lookup','--noinput'])
