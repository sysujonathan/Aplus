using System;
using System.IO;
using System.Diagnostics;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using System.Threading;

class Launcher {
    [STAThread] static int Main() {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            try {
                using (Mutex updating = Mutex.OpenExisting(@"Local\AplusInstalledUpdate")) {
                    throw new Exception("Aplus 正在更新，请稍候再打开。");
                }
            } catch (WaitHandleCannotBeOpenedException) { }
            string version = File.ReadAllText(Path.Combine(root, "current.txt")).Trim();
            if (!Regex.IsMatch(version, @"^\d{4}\.\d{1,2}\.\d{1,2}\.\d+$"))
                throw new Exception("当前版本指针无效。");
            string exe = Path.Combine(root, "app", version, "AplusApp.exe");
            if (!File.Exists(exe)) throw new Exception("程序文件不完整，请重新运行安装包；原数据不受影响。");
            if (File.Exists(Path.Combine(root,"update-pending.json"))) {
                var recovery = new ProcessStartInfo(exe, "--recover-installed \""+root.TrimEnd('\\')+"\"");
                recovery.UseShellExecute=false;
                recovery.EnvironmentVariables["APLUS_INSTALL_ROOT"]=root;
                using (var helper=Process.Start(recovery)) {
                    if (!helper.WaitForExit(30000) || helper.ExitCode!=0)
                        throw new Exception("Aplus 正在完成更新确认，请稍候。原数据已保留。");
                }
                version=File.ReadAllText(Path.Combine(root,"current.txt")).Trim();
                if (!Regex.IsMatch(version,@"^\d{4}\.\d{1,2}\.\d{1,2}\.\d+$")) throw new Exception("版本指针无效。");
                exe=Path.Combine(root,"app",version,"AplusApp.exe");
            }
            var start = new ProcessStartInfo(exe);
            start.UseShellExecute = false;
            start.WorkingDirectory = Path.GetDirectoryName(exe);
            start.EnvironmentVariables["APLUS_INSTALL_ROOT"] = root;
            Process.Start(start);
            return 0;
        } catch(Exception ex) {
            MessageBox.Show(ex.Message, "Aplus 启动提示", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
