using System;
using System.IO;
using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using System.Threading;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Security.Cryptography;
using System.Runtime.InteropServices;

class Launcher {
    static JavaScriptSerializer Json = new JavaScriptSerializer();
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
    static extern bool MoveFileEx(string oldFile,string newFile,int flags);
    static string Full(string path) { return Path.GetFullPath(path).TrimEnd('\\'); }
    static bool Same(string a,string b) { return String.Equals(Full(a),Full(b),StringComparison.OrdinalIgnoreCase); }
    static Dictionary<string,object> Read(string path) { return Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path)); }
    static string Text(Dictionary<string,object> data,string key) {
        return data.ContainsKey(key) ? Convert.ToString(data[key]) : "";
    }
    static bool Version(string value) {
        if(!Regex.IsMatch(value,@"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[1-9][0-9]{0,3}\z")) return false;
        try { var p=value.Split('.');new DateTime(Int32.Parse(p[0]),Int32.Parse(p[1]),Int32.Parse(p[2]));return true; }
        catch(ArgumentOutOfRangeException) { return false; }
    }
    static bool Number(Dictionary<string,object> data,string key,long min,long max) {
        if(!data.ContainsKey(key) || !(data[key] is int || data[key] is long)) return false;
        long n=Convert.ToInt64(data[key]);return n>=min && n<=max;
    }
    static bool Manifest(Dictionary<string,object> target) {
        string version=Text(target,"version");
        if(!Version(version) || !Number(target,"protocol",1,1) || Text(target,"channel")!="stable" ||
           !Number(target,"database_schema",9,9) || !target.ContainsKey("preview") ||
           !(target["preview"] is bool) || (bool)target["preview"] ||
           !Regex.IsMatch(Text(target,"commit"),@"^[0-9a-f]{40}\z")) return false;
        var p=version.Split('.');long sequence=Int64.Parse(p[0]+p[1]+p[2])*10000+Int64.Parse(p[3]);
        if(!Number(target,"sequence",sequence,sequence) || !target.ContainsKey("package")) return false;
        var package=target["package"] as Dictionary<string,object>;
        return package!=null && Number(package,"bytes",1,2000000000) &&
            Regex.IsMatch(Text(package,"sha256"),@"^[0-9a-f]{64}\z") && Text(package,"url")==
            "https://github.com/sysujonathan/Aplus/releases/download/v"+version+"/AplusSetup-"+version+"-x64.exe";
    }
    static Mutex Gate(string name) {
        bool created;
        var gate=new Mutex(false,name,out created);
        if (!created) { gate.Dispose(); throw new Exception("Aplus 安装或更新仍在进行，请稍后再打开。"); }
        return gate;
    }
    static string Hash(string path) {
        using(var stream=File.OpenRead(path)) using(var sha=SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-","").ToLowerInvariant();
    }
    static void Atomic(string path,string content) {
        string temp=path+".new";
        File.WriteAllText(temp,content,new UTF8Encoding(false));
        if (!MoveFileEx(temp,path,9)) throw new Exception("无法保存恢复结果；原数据与恢复记录已保留。");
    }
    static bool Active(string executable) {
        foreach(var process in Process.GetProcessesByName("AplusApp")) using(process) {
            if(process.Id==Process.GetCurrentProcess().Id) continue;
            try { if(Same(process.MainModule.FileName,executable)) return true; }
            catch(InvalidOperationException) { continue; } // Exited during enumeration.
            catch { throw new Exception("无法确认新版进程身份，已保留恢复现场。"); }
        }
        return false;
    }
    static bool Alive(int pid) {
        try { using(var p=Process.GetProcessById(pid)) return !p.HasExited; }
        catch(ArgumentException) { return false; }
    }
    static void Recover(string root) {
        string pending=Path.Combine(root,"update-pending.json");
        if(!File.Exists(pending)) return;
        using(Gate(@"Local\AplusInstalledUpdate")) using(Gate(@"Local\AplusInstalledSetup")) {
            var record=Read(pending);
            string folder=Text(record,"folder");
            string home=Environment.GetEnvironmentVariable("APLUS_INSTALL_STATE");
            if(String.IsNullOrEmpty(home)) home=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Aplus","installed");
            if(!Same(Path.GetDirectoryName(Full(folder)),Path.Combine(home,"updates")))
                throw new Exception("更新恢复位置无效，原数据已保留。");
            var request=Read(Path.Combine(folder,"request.json"));
            var target=(Dictionary<string,object>)request["target"];
            string version=Text(target,"version");
            if(!Same(Text(request,"install_root"),root) || !Manifest(target))
                throw new Exception("更新恢复身份不一致，原数据已保留。");
            string phase=Text(record,"phase");
            if(phase!="installing" && phase!="installed" && phase!="launching" && phase!="startup_wait")
                throw new Exception("恢复信息不完整，请保留现场并联系维护者；原数据已保留。");
            string previous=Text(record,"previous");
            if(!Version(previous)) throw new Exception("旧版本指针无效，原数据已保留。");
            string ack=Path.Combine(folder,"started.json");
            if(File.Exists(ack)) {
                try {
                    var started=Read(ack);
                    if(started.ContainsKey("started") && started["started"] is bool && (bool)started["started"] &&
                       Text(started,"version")==version && Text(started,"commit")==Text(target,"commit") &&
                       Same(Text(started,"runtime"),Text(request,"runtime"))) {
                        Atomic(Path.Combine(folder,"receipt.json"),Json.Serialize(new {state="completed",version=version}));
                        File.Delete(pending);
                        try {
                            string setup=Path.Combine(folder,"setup.exe");
                            var package=(Dictionary<string,object>)target["package"];
                            if(File.Exists(setup) && new FileInfo(setup).Length==Convert.ToInt64(package["bytes"]) &&
                               Hash(setup)==Text(package,"sha256")) File.Delete(setup);
                        } catch(IOException) { } catch(UnauthorizedAccessException) { }
                        return;
                    }
                } catch(ArgumentException) { } // Malformed ACK is not success.
            }
            int pid=record.ContainsKey("pid") ? Convert.ToInt32(record["pid"]) : 0;
            if((pid!=0 && Alive(pid)) || Active(Path.Combine(root,"app",version,"AplusApp.exe")))
                throw new Exception("新版仍在运行并等待启动确认，请稍后；原数据已保留。");
            string oldExe=Path.Combine(root,"app",previous,"AplusApp.exe");
            string expected=Text(record,"previous_sha256");
            if(!File.Exists(oldExe) || expected.Length!=64 || Hash(oldExe)!=expected)
                throw new Exception("旧版本身份未能确认，请保留恢复现场。");
            Atomic(Path.Combine(root,"current.txt"),previous);
            Atomic(Path.Combine(folder,"receipt.json"),Json.Serialize(new {state="rolled_back",previous=previous}));
            File.Delete(pending);
        }
    }
    [STAThread] static int Main(string[] args) {
        try {
            using(var launcher=new Mutex(false,@"Local\AplusInstalledLauncher")) {
                string root=AppDomain.CurrentDomain.BaseDirectory;
                // Recovery is independent of the current version's Python/EXE.
                // A missing or broken current payload cannot hide the old copy.
                Recover(root);
                if(args.Length==1 && args[0]=="--recover-only") return 0;
                using(var updating=Gate(@"Local\AplusInstalledUpdate")) { }
                string version=File.ReadAllText(Path.Combine(root,"current.txt")).Trim();
                if(!Version(version)) throw new Exception("当前版本指针无效。");
                string exe=Path.Combine(root,"app",version,"AplusApp.exe");
                if(!File.Exists(exe)) throw new Exception("程序文件不完整，请重新运行安装包；原数据不受影响。");
                var start=new ProcessStartInfo(exe);
                start.UseShellExecute=false;
                start.WorkingDirectory=Path.GetDirectoryName(exe);
                // Do not materialize .NET's case-insensitive environment map:
                // inherited HTTPS_PROXY + https_proxy can make its getter throw.
                // Inherit the native block, adding only our own process variable.
                Environment.SetEnvironmentVariable("APLUS_INSTALL_ROOT",root);
                Process.Start(start);return 0;
            }
        } catch(Exception ex) {
            if(args.Length==0) MessageBox.Show(ex.Message,"Aplus 启动提示",MessageBoxButtons.OK,MessageBoxIcon.Error);
            return 1;
        }
    }
}
