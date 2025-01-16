# The helper class helps us to execute outside programs.
import subprocess

from ..config.main_config import *


class CommandUtils:

    @classmethod
    def OpenGeneratedModFolder(cls):
        '''
        This will be call after generate mod, it will open explorer and shows the result mod files generated.
        '''
        if GenerateModConfig.open_generated_mod_folder_after_run():
            generated_mod_folder_path = MainConfig.path_generate_mod_folder()

            # XXX 不能使用subprocess.run('explorer',path)的方式打开文件夹，否则部分用户的电脑上无法识别到路径，且自动打开 文档 文件夹。
            # if " " in generated_mod_folder_path:
            #     generated_mod_folder_path = '"{}"'.format(generated_mod_folder_path)
            # print("generated_mod_folder_path: " + generated_mod_folder_path)
            # subprocess.run(['explorer',generated_mod_folder_path])
            os.startfile(generated_mod_folder_path)



