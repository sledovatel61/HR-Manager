// HR Manager - portable license issuer launcher (single file, owner PC).
//
// What it is:
//   A tiny self-extracting launcher. The built .exe = this launcher + a ZIP
//   payload appended after a fixed-size trailer. The payload contains the
//   embeddable Python (with cryptography) and the issuer app (gui.py/cli.py),
//   so the owner needs NOTHING installed: no Python, no Node, no Docker, no
//   Visual Studio, no internet.
//
// Double-click  -> extracts once to %LOCALAPPDATA%\HRManager\LicenseIssuer\
//                  payload-<size> and opens the GUI (pythonw.exe gui.py).
// With args     -> runs the bundled CLI (python.exe cli.py <args...>) and
//                  returns its exit code (used by CI and by the owner for
//                  "verify" / "gen-keypair" / "issue").
// --hrm-selfcheck <json> -> verifies the extraction and writes a small JSON
//                  report (CI evidence), never touches keys.
//
// Security notes (must stay true - CI greps this file):
//   * the launcher never reads, writes, logs or copies private keys;
//   * it never contacts the network;
//   * it only writes below %LOCALAPPDATA%\HRManager\LicenseIssuer.
//
// ENCODING CONTRACT: this file is ASCII-only on purpose. It is compiled by the
// csc.exe that ships with Windows (.NET Framework), which reads the source in
// the system code page; Cyrillic strings are therefore spelled as \uXXXX
// escapes so the build is identical on any Windows locale.

using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Windows.Forms;

internal static class Program
{
    // 18-byte marker + 14 ASCII digits with the payload length = 32-byte trailer.
    private const string PayloadMarker = "HRMISSUER-PAYLOAD1";
    private const int TrailerSize = 32;
    private const int LengthDigits = 14;
    private const string ReadyFileName = ".payload-ready";
    private const string ProductFolder = "HRManager";

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            string exePath = Process.GetCurrentProcess().MainModule.FileName;

            if (args.Length > 0 && args[0] == "--hrm-version")
            {
                // Console-free informational mode: message box for a double-click,
                // exit code 0 so scripts can rely on it.
                MessageBox.Show(
                    "HR Manager - portable license issuer\r\n" + LauncherVersion() + "\r\n" + exePath,
                    "HR Manager - licenses",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
                return 0;
            }

            bool showProgress = args.Length == 0;
            string payloadDir = ExtractPayload(exePath, showProgress);
            string appDir = Path.Combine(payloadDir, "license-issuer");
            string pythonDir = Path.Combine(payloadDir, "python");
            string pythonExe = Path.Combine(pythonDir, "python.exe");
            string pythonwExe = Path.Combine(pythonDir, "pythonw.exe");
            string guiScript = Path.Combine(appDir, "gui.py");
            string cliScript = Path.Combine(appDir, "cli.py");

            if (args.Length > 0 && args[0] == "--hrm-selfcheck")
            {
                string outPath = args.Length > 1
                    ? args[1]
                    : Path.Combine(LocalRoot(), "license-issuer-selfcheck.json");
                return SelfCheck(exePath, payloadDir, appDir, pythonDir, outPath);
            }

            if (args.Length == 0)
            {
                if (!File.Exists(guiScript))
                {
                    ShowError(UiStartupFailed() + "\r\n\r\ngui.py: " + guiScript);
                    return 3;
                }
                if (!File.Exists(pythonwExe)) { pythonwExe = pythonExe; }
                if (!File.Exists(pythonwExe))
                {
                    ShowError(UiStartupFailed() + "\r\n\r\npython: " + pythonDir);
                    return 3;
                }
                ProcessStartInfo gui = new ProcessStartInfo();
                gui.FileName = pythonwExe;
                gui.Arguments = Quote(guiScript);
                gui.WorkingDirectory = appDir;
                gui.UseShellExecute = true;
                Process.Start(gui);
                return 0;
            }

            if (!File.Exists(cliScript))
            {
                ShowError(UiStartupFailed() + "\r\n\r\ncli.py: " + cliScript);
                return 3;
            }
            if (!File.Exists(pythonExe))
            {
                ShowError(UiStartupFailed() + "\r\n\r\npython: " + pythonDir);
                return 3;
            }
            string logPath = Environment.GetEnvironmentVariable("HRM_PORTABLE_LOG");
            if (!string.IsNullOrEmpty(logPath))
            {
                // Diagnostic/CI mode: same CLI, output captured to a file, no console window.
                return RunCliCaptured(pythonExe, cliScript, args, appDir, logPath);
            }
            ProcessStartInfo cli = new ProcessStartInfo();
            cli.FileName = pythonExe;
            cli.Arguments = Quote(cliScript) + " " + JoinArguments(args);
            cli.WorkingDirectory = appDir;
            cli.UseShellExecute = true; // the child console app gets its own window
            Process child = Process.Start(cli);
            child.WaitForExit();
            return child.ExitCode;
        }
        catch (Exception ex)
        {
            ShowError(UiCrash() + "\r\n\r\n" + ex.Message);
            return 4;
        }
    }

    private static int RunCliCaptured(string pythonExe, string cliScript, string[] args, string appDir, string logPath)
    {
        // Runs the bundled CLI with stdout/stderr captured into logPath (UTF-8, no BOM).
        // The launcher itself never writes keys anywhere: this is the child's own output.
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = pythonExe;
        psi.Arguments = Quote(cliScript) + " " + JoinArguments(args);
        psi.WorkingDirectory = appDir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        StringBuilder buffer = new StringBuilder();
        Process child = new Process();
        child.StartInfo = psi;
        child.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data != null) { lock (buffer) { buffer.AppendLine(e.Data); } }
        };
        child.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data != null) { lock (buffer) { buffer.AppendLine("[stderr] " + e.Data); } }
        };
        child.Start();
        child.BeginOutputReadLine();
        child.BeginErrorReadLine();
        child.WaitForExit();
        child.WaitForExit(); // flush the asynchronous readers
        string text;
        lock (buffer) { text = buffer.ToString(); }
        try
        {
            string parent = Path.GetDirectoryName(logPath);
            if (!string.IsNullOrEmpty(parent)) { Directory.CreateDirectory(parent); }
            File.WriteAllText(logPath, text, new UTF8Encoding(false));
        }
        catch (Exception)
        {
            // A diagnostic log must never break the command itself.
        }
        return child.ExitCode;
    }

    private static string LocalRoot()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            ProductFolder,
            "LicenseIssuer");
    }

    private static string Quote(string value)
    {
        return "\"" + value + "\"";
    }

    private static string JoinArguments(string[] args)
    {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < args.Length; i++)
        {
            if (i > 0) { sb.Append(' '); }
            string a = args[i];
            bool needsQuotes = a.Length == 0 || a.IndexOf(' ') >= 0 || a.IndexOf('"') >= 0;
            if (!needsQuotes) { sb.Append(a); continue; }
            sb.Append('"');
            sb.Append(a.Replace("\"", "\\\""));
            sb.Append('"');
        }
        return sb.ToString();
    }

    private static string LauncherVersion()
    {
        try
        {
            return "Version " + System.Reflection.Assembly.GetExecutingAssembly().GetName().Version.ToString();
        }
        catch (Exception)
        {
            return "Version unknown";
        }
    }

    private static long ReadPayloadLength(FileStream fs)
    {
        if (fs.Length <= TrailerSize) { throw new InvalidDataException("file is too small to contain a payload trailer"); }
        byte[] trailer = new byte[TrailerSize];
        fs.Seek(fs.Length - TrailerSize, SeekOrigin.Begin);
        int read = 0;
        while (read < TrailerSize)
        {
            int n = fs.Read(trailer, read, TrailerSize - read);
            if (n <= 0) { throw new InvalidDataException("could not read the payload trailer"); }
            read += n;
        }
        string marker = Encoding.ASCII.GetString(trailer, 0, PayloadMarker.Length);
        if (marker != PayloadMarker)
        {
            throw new InvalidDataException("this file does not contain an HR Manager issuer payload");
        }
        string digits = Encoding.ASCII.GetString(trailer, PayloadMarker.Length, LengthDigits);
        long length = long.Parse(digits, CultureInfo.InvariantCulture);
        if (length <= 0 || length > fs.Length - TrailerSize)
        {
            throw new InvalidDataException("payload length is out of range: " + digits);
        }
        return length;
    }

    private static string ExtractPayload(string exePath, bool showProgress)
    {
        FileStream fs = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        try
        {
            long payloadLength = ReadPayloadLength(fs);
            long payloadOffset = fs.Length - TrailerSize - payloadLength;
            string payloadDir = Path.Combine(LocalRoot(), "payload-" + payloadLength.ToString(CultureInfo.InvariantCulture));
            string readyFile = Path.Combine(payloadDir, ReadyFileName);
            if (File.Exists(readyFile) && File.Exists(Path.Combine(payloadDir, "license-issuer", "gui.py")))
            {
                return payloadDir;
            }

            Form progressForm = null;
            Label progressLabel = null;
            ProgressBar progressBar = null;
            if (showProgress)
            {
                try
                {
                    progressForm = new Form();
                    progressForm.Text = UiProgressTitle();
                    progressForm.FormBorderStyle = FormBorderStyle.FixedDialog;
                    progressForm.StartPosition = FormStartPosition.CenterScreen;
                    progressForm.ClientSize = new System.Drawing.Size(420, 110);
                    progressForm.MinimizeBox = false;
                    progressForm.MaximizeBox = false;
                    progressForm.ControlBox = false;
                    progressLabel = new Label();
                    progressLabel.Text = UiPreparing();
                    progressLabel.AutoSize = true;
                    progressLabel.Location = new System.Drawing.Point(16, 20);
                    progressForm.Controls.Add(progressLabel);
                    progressBar = new ProgressBar();
                    progressBar.Location = new System.Drawing.Point(18, 60);
                    progressBar.Size = new System.Drawing.Size(384, 22);
                    progressBar.Style = ProgressBarStyle.Continuous;
                    progressForm.Controls.Add(progressBar);
                    progressForm.Show();
                    Application.DoEvents();
                }
                catch (Exception)
                {
                    // A progress window is a nicety; extraction must go on without it.
                    progressForm = null;
                }
            }

            try
            {
                if (Directory.Exists(payloadDir)) { TryDeleteDirectory(payloadDir); }
                Directory.CreateDirectory(payloadDir);
                long extracted = 0;
                int entries = 0;
                fs.Seek(payloadOffset, SeekOrigin.Begin);
                using (ZipArchive archive = new ZipArchive(fs, ZipArchiveMode.Read, true))
                {
                    int total = archive.Entries.Count;
                    if (total <= 0) { throw new InvalidDataException("payload archive is empty"); }
                    SetProgress(progressBar, 0);
                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        string name = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                        string target = Path.Combine(payloadDir, name);
                        if (entry.Name.Length == 0)
                        {
                            Directory.CreateDirectory(target);
                            continue;
                        }
                        string parent = Path.GetDirectoryName(target);
                        if (!string.IsNullOrEmpty(parent)) { Directory.CreateDirectory(parent); }
                        using (Stream source = entry.Open())
                        using (FileStream destination = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None))
                        {
                            byte[] buffer = new byte[65536];
                            int read;
                            while ((read = source.Read(buffer, 0, buffer.Length)) > 0)
                            {
                                destination.Write(buffer, 0, read);
                                extracted += read;
                            }
                        }
                        entries++;
                        if (progressBar != null)
                        {
                            int percent = total > 0 ? (int)((entries * 100L) / total) : 0;
                            if (percent > 100) { percent = 100; }
                            SetProgress(progressBar, percent);
                            if (progressLabel != null) { progressLabel.Text = UiPreparing() + " " + percent.ToString(CultureInfo.InvariantCulture) + "%"; }
                            Application.DoEvents();
                        }
                    }
                }
                File.WriteAllText(
                    Path.Combine(payloadDir, ReadyFileName),
                    "entries=" + entries.ToString(CultureInfo.InvariantCulture) + "\r\nbytes=" + extracted.ToString(CultureInfo.InvariantCulture) + "\r\n",
                    Encoding.UTF8);
                if (progressLabel != null) { progressLabel.Text = UiReady(); }
                if (progressBar != null) { SetProgress(progressBar, 100); }
                Application.DoEvents();
                return payloadDir;
            }
            finally
            {
                if (progressForm != null)
                {
                    try { progressForm.Close(); progressForm.Dispose(); }
                    catch (Exception) { }
                }
            }
        }
        finally
        {
            fs.Dispose();
        }
    }

    private static void SetProgress(ProgressBar bar, int value)
    {
        if (bar == null) { return; }
        try
        {
            if (value < 0) { value = 0; }
            if (value > bar.Maximum) { value = bar.Maximum; }
            bar.Value = value;
        }
        catch (Exception)
        {
        }
    }

    private static void TryDeleteDirectory(string path)
    {
        try { Directory.Delete(path, true); }
        catch (Exception) { }
    }

    private static int SelfCheck(string exePath, string payloadDir, string appDir, string pythonDir, string outPath)
    {
        string[] required = new string[]
        {
            Path.Combine(appDir, "gui.py"),
            Path.Combine(appDir, "cli.py"),
            Path.Combine(appDir, "license_issuer.py"),
            Path.Combine(pythonDir, "python.exe"),
            Path.Combine(pythonDir, "pythonw.exe")
        };
        bool ok = true;
        StringBuilder missing = new StringBuilder();
        for (int i = 0; i < required.Length; i++)
        {
            if (!File.Exists(required[i]))
            {
                ok = false;
                missing.Append((missing.Length > 0 ? ";" : "") + Path.GetFileName(required[i]));
            }
        }
        int fileCount = 0;
        long totalBytes = 0;
        try
        {
            foreach (string file in Directory.GetFiles(payloadDir, "*", SearchOption.AllDirectories))
            {
                fileCount++;
                totalBytes += new FileInfo(file).Length;
            }
        }
        catch (Exception)
        {
        }

        StringBuilder json = new StringBuilder();
        json.Append("{\r\n");
        json.Append("  \"ok\": " + (ok ? "true" : "false") + ",\r\n");
        json.Append("  \"exe\": " + Json(exePath) + ",\r\n");
        json.Append("  \"payload_dir\": " + Json(payloadDir) + ",\r\n");
        json.Append("  \"payload_file_count\": " + fileCount.ToString(CultureInfo.InvariantCulture) + ",\r\n");
        json.Append("  \"payload_total_bytes\": " + totalBytes.ToString(CultureInfo.InvariantCulture) + ",\r\n");
        json.Append("  \"missing\": " + Json(missing.ToString()) + ",\r\n");
        json.Append("  \"launcher\": " + Json(LauncherVersion()) + "\r\n");
        json.Append("}\r\n");
        string parent = Path.GetDirectoryName(outPath);
        if (!string.IsNullOrEmpty(parent)) { Directory.CreateDirectory(parent); }
        File.WriteAllText(outPath, json.ToString(), Encoding.UTF8);
        return ok ? 0 : 5;
    }

    private static string Json(string value)
    {
        if (value == null) { return "null"; }
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < value.Length; i++)
        {
            char c = value[i];
            if (c == '\\') { sb.Append("\\\\"); }
            else if (c == '"') { sb.Append("\\\"") ; }
            else if (c < ' ') { sb.Append("\\u" + ((int)c).ToString("x4", CultureInfo.InvariantCulture)); }
            else { sb.Append(c); }
        }
        sb.Append('"');
        return sb.ToString();
    }

    private static void ShowError(string message)
    {
        try
        {
            MessageBox.Show(message, UiErrorTitle(), MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        catch (Exception)
        {
        }
    }

    // Cyrillic UI text as \uXXXX escapes (the source file itself stays ASCII).
    private static string UiProgressTitle()
    {
        return "\u0417\u0430\u043f\u0443\u0441\u043a \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u044b \u0432\u044b\u043f\u0443\u0441\u043a\u0430 \u043b\u0438\u0446\u0435\u043d\u0437\u0438\u0439";
    }

    private static string UiPreparing()
    {
        return "\u041f\u043e\u0434\u0433\u043e\u0442\u0430\u0432\u043b\u0438\u0432\u0430\u0435\u043c \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0443 (\u043f\u0435\u0440\u0432\u044b\u0439 \u0437\u0430\u043f\u0443\u0441\u043a, 10-30 \u0441\u0435\u043a\u0443\u043d\u0434)...";
    }

    private static string UiReady()
    {
        return "\u041f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0430 \u0433\u043e\u0442\u043e\u0432\u0430. \u041e\u0442\u043a\u0440\u044b\u0432\u0430\u0435\u043c \u043e\u043a\u043d\u043e...";
    }

    private static string UiErrorTitle()
    {
        return "HR Manager - \u043b\u0438\u0446\u0435\u043d\u0437\u0438\u0438";
    }

    private static string UiStartupFailed()
    {
        return "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u0442\u044c \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0443 \u0432\u044b\u043f\u0443\u0441\u043a\u0430 \u043b\u0438\u0446\u0435\u043d\u0437\u0438\u0439.";
    }

    private static string UiCrash()
    {
        return "\u041f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0430 \u0432\u044b\u043f\u0443\u0441\u043a\u0430 \u043b\u0438\u0446\u0435\u043d\u0437\u0438\u0439 \u0437\u0430\u0432\u0435\u0440\u0448\u0438\u043b\u0430\u0441\u044c \u0441 \u043e\u0448\u0438\u0431\u043a\u043e\u0439.";
    }
}
