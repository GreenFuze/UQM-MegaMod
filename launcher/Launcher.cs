// The window a player sees before the game starts.
//
// Pasting an API key, installing Python packages and picking a local model
// are not things that belong in a 1992-style in-game menu, and they are not
// things this audience should meet a console for either. So: a plain form,
// double-clicked.
//
// Built against .NET Framework 4.8 with the csc.exe that ships in Windows,
// so it needs no runtime installed, no SDK to build, and no Visual Studio.
// It must also run BEFORE Python exists, which rules out writing it in
// Python - that is the whole chicken-and-egg this solves.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace UqmAi
{
    // One prerequisite and what to do about it.
    class Check
    {
        public string Name;
        public bool Ok;
        public string Detail;
        public bool Fixable;      // true when "Install what's missing" can help
    }

    // Reads and writes uqmai.toml. Deliberately not a TOML library: this file
    // is written here and parsed by the sidecar, and the shapes are trivial.
    class Settings
    {
        public string Provider = "claude";
        public string ApiKey = "";
        public string Model = "";
        public string BaseUrl = "";
        public bool UseSubscription = false;
        public bool Voice = false;

        public static string Path
        {
            get
            {
                string appdata = Environment.GetFolderPath(
                    Environment.SpecialFolder.ApplicationData);
                return System.IO.Path.Combine(appdata, "uqm-megamod", "uqmai.toml");
            }
        }

        public static Settings Load()
        {
            Settings s = new Settings();
            if (!File.Exists(Path)) return s;

            foreach (string raw in File.ReadAllLines(Path))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                int eq = line.IndexOf('=');
                if (eq <= 0) continue;

                string key = line.Substring(0, eq).Trim();
                string val = line.Substring(eq + 1).Trim().Trim('"');
                switch (key)
                {
                    case "provider": s.Provider = val; break;
                    case "api_key": s.ApiKey = val; break;
                    case "model": s.Model = val; break;
                    case "base_url": s.BaseUrl = val; break;
                    case "use_subscription": s.UseSubscription = val == "true"; break;
                    case "voice": s.Voice = val == "true"; break;
                }
            }
            return s;
        }

        public void Save()
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path));
            StringBuilder text = new StringBuilder();
            text.AppendLine("# UQM MegaMod AI - written by the launcher.");
            text.AppendLine("# Environment variables override anything here.");
            text.AppendLine();
            text.AppendLine("provider = \"" + Provider + "\"");
            if (ApiKey.Length > 0) text.AppendLine("api_key = \"" + ApiKey + "\"");
            if (Model.Length > 0) text.AppendLine("model = \"" + Model + "\"");
            if (BaseUrl.Length > 0) text.AppendLine("base_url = \"" + BaseUrl + "\"");
            text.AppendLine("use_subscription = " + (UseSubscription ? "true" : "false"));
            text.AppendLine("voice = " + (Voice ? "true" : "false"));

            // No BOM: tomllib rejects one.
            File.WriteAllText(Path, text.ToString(), new UTF8Encoding(false));
        }
    }

    class Launcher : Form
    {
        // Laid out relative to the launcher, which sits beside the game.
        readonly string root;
        string VenvPython { get { return Path.Combine(root, @"ai\.venv\Scripts\python.exe"); } }
        string AiDir      { get { return Path.Combine(root, "ai"); } }
        string GameExe    { get { return Path.Combine(root, "UrQuanMasters.exe"); } }
        string ContentDir
        {
            get
            {
                return Path.Combine(Directory.GetParent(root).FullName,
                                    @"uqm-megamod-content\base\comm");
            }
        }

        Settings settings;
        string systemPython;

        // Widgets that need reading later.
        RadioButton rbClaudeKey, rbClaudeSub, rbOpenAi, rbLocal, rbNone;
        TextBox tbKey;
        ComboBox cbModel;
        CheckBox cbVoice;
        Label lbStatus;
        TextBox tbLog;
        Button btInstall, btTest, btPlay, btRecheck;
        ListView lvChecks;

        // Starting points, with the download size - the number that actually
        // decides whether someone tries this. NOT rankings: none of these has
        // been played through, so the notes say what they are (size, RAM) and
        // not which is better, which would be a claim nobody has checked.
        static readonly string[] LocalModels = {
            "qwen2.5:7b        (~4.7 GB download, 8 GB RAM)",
            "llama3.1:8b       (~4.7 GB download, 8 GB RAM)",
            "mistral-nemo:12b  (~7.1 GB download, 12 GB RAM)",
            "qwen2.5:14b       (~9.0 GB download, 16 GB RAM)",
        };

        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new Launcher());
        }

        public Launcher()
        {
            root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            settings = Settings.Load();
            BuildUi();

            // Checking costs several process launches, and on a machine with
            // no Python at all it costs two timeouts. Doing that before the
            // window is shown means a player double-clicks and stares at
            // nothing, so the form paints first and the checks follow.
            Shown += delegate { RefreshChecks(); };
        }

        void BuildUi()
        {
            Text = "The Ur-Quan Masters MegaMod AI - Setup";
            Size = new Size(700, 660);
            MinimumSize = new Size(640, 600);
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Segoe UI", 9F);
            BackColor = Color.FromArgb(250, 250, 250);

            Label title = new Label();
            title.Text = "Talk to the aliens, in your own words";
            title.Font = new Font("Segoe UI", 13F, FontStyle.Bold);
            title.SetBounds(16, 12, 640, 26);
            Controls.Add(title);

            Label sub = new Label();
            sub.Text = "Proof of concept. Set up once, then press Play.";
            sub.ForeColor = Color.Gray;
            sub.SetBounds(18, 38, 640, 18);
            Controls.Add(sub);

            // --- prerequisites -------------------------------------------
            GroupBox gbChecks = new GroupBox();
            gbChecks.Text = "Prerequisites";
            gbChecks.SetBounds(16, 64, 654, 150);
            gbChecks.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Controls.Add(gbChecks);

            lvChecks = new ListView();
            lvChecks.View = View.Details;
            lvChecks.FullRowSelect = true;
            lvChecks.HeaderStyle = ColumnHeaderStyle.Nonclickable;
            lvChecks.Columns.Add("", 24);
            lvChecks.Columns.Add("Requirement", 200);
            lvChecks.Columns.Add("Detail", 400);
            lvChecks.SetBounds(12, 20, 630, 90);
            lvChecks.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            gbChecks.Controls.Add(lvChecks);

            btRecheck = new Button();
            btRecheck.Text = "Check again";
            btRecheck.SetBounds(12, 116, 110, 26);
            btRecheck.Click += delegate { RefreshChecks(); };
            gbChecks.Controls.Add(btRecheck);

            btInstall = new Button();
            btInstall.Text = "Install what's missing";
            btInstall.SetBounds(130, 116, 160, 26);
            btInstall.Click += delegate { StartWork(InstallMissing); };
            gbChecks.Controls.Add(btInstall);

            lbStatus = new Label();
            lbStatus.SetBounds(300, 121, 330, 18);
            lbStatus.ForeColor = Color.Gray;
            gbChecks.Controls.Add(lbStatus);

            // --- which AI -------------------------------------------------
            GroupBox gbAi = new GroupBox();
            gbAi.Text = "Which AI answers the aliens";
            gbAi.SetBounds(16, 222, 654, 190);
            gbAi.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Controls.Add(gbAi);

            rbClaudeKey = AddRadio(gbAi, "Claude, with an API key   (billed per conversation)", 22);
            rbClaudeSub = AddRadio(gbAi, "Claude, with my subscription   (personal use only - see below)", 44);
            rbOpenAi    = AddRadio(gbAi, "OpenAI, with an API key   (billed per conversation)", 66);
            rbLocal     = AddRadio(gbAi, "A local model   (free, private, nothing leaves this PC)", 88);
            rbNone      = AddRadio(gbAi, "No AI - play as plain MegaMod", 110);

            Label lbKey = new Label();
            lbKey.Text = "API key:";
            lbKey.SetBounds(34, 138, 60, 20);
            gbAi.Controls.Add(lbKey);

            tbKey = new TextBox();
            tbKey.SetBounds(96, 135, 400, 22);
            tbKey.UseSystemPasswordChar = true;
            tbKey.Text = settings.ApiKey;
            gbAi.Controls.Add(tbKey);

            Label lbModel = new Label();
            lbModel.Text = "Model:";
            lbModel.SetBounds(34, 164, 60, 20);
            gbAi.Controls.Add(lbModel);

            cbModel = new ComboBox();
            cbModel.SetBounds(96, 161, 400, 22);
            cbModel.DropDownStyle = ComboBoxStyle.DropDown;
            cbModel.Items.AddRange(LocalModels);
            gbAi.Controls.Add(cbModel);

            EventHandler refresh = delegate { UpdateAiFields(); };
            rbClaudeKey.CheckedChanged += refresh;
            rbClaudeSub.CheckedChanged += refresh;
            rbOpenAi.CheckedChanged += refresh;
            rbLocal.CheckedChanged += refresh;
            rbNone.CheckedChanged += refresh;

            // --- voice ----------------------------------------------------
            cbVoice = new CheckBox();
            cbVoice.Text = "Synthesised voice (large download, off by default - subtitles otherwise)";
            cbVoice.SetBounds(20, 420, 600, 22);
            cbVoice.Checked = settings.Voice;
            Controls.Add(cbVoice);

            // --- log ------------------------------------------------------
            tbLog = new TextBox();
            tbLog.Multiline = true;
            tbLog.ScrollBars = ScrollBars.Vertical;
            tbLog.ReadOnly = true;
            tbLog.BackColor = Color.White;
            tbLog.Font = new Font("Consolas", 8.5F);
            tbLog.SetBounds(16, 448, 654, 118);
            tbLog.Anchor = AnchorStyles.Top | AnchorStyles.Bottom
                         | AnchorStyles.Left | AnchorStyles.Right;
            Controls.Add(tbLog);

            // --- actions --------------------------------------------------
            btTest = new Button();
            btTest.Text = "Test connection";
            btTest.SetBounds(16, 576, 130, 32);
            btTest.Anchor = AnchorStyles.Bottom | AnchorStyles.Left;
            btTest.Click += delegate { SaveFromUi(); StartWork(TestConnection); };
            Controls.Add(btTest);

            btPlay = new Button();
            btPlay.Text = "Play";
            btPlay.Font = new Font("Segoe UI", 10F, FontStyle.Bold);
            btPlay.SetBounds(556, 576, 114, 32);
            btPlay.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
            btPlay.Click += delegate { SaveFromUi(); Play(); };
            Controls.Add(btPlay);

            LoadIntoUi();
        }

        RadioButton AddRadio(Control parent, string text, int y)
        {
            RadioButton rb = new RadioButton();
            rb.Text = text;
            rb.SetBounds(16, y, 620, 20);
            parent.Controls.Add(rb);
            return rb;
        }

        void LoadIntoUi()
        {
            if (settings.Provider == "mock") rbNone.Checked = true;
            else if (settings.Provider == "openai") rbOpenAi.Checked = true;
            else if (settings.Provider == "local") rbLocal.Checked = true;
            else if (settings.UseSubscription) rbClaudeSub.Checked = true;
            else rbClaudeKey.Checked = true;

            if (settings.Model.Length > 0) cbModel.Text = settings.Model;
            UpdateAiFields();
        }

        void UpdateAiFields()
        {
            bool needsKey = rbClaudeKey.Checked || rbOpenAi.Checked;
            tbKey.Enabled = needsKey;
            cbModel.Enabled = rbLocal.Checked || rbOpenAi.Checked;

            if (rbLocal.Checked && cbModel.Text.Length == 0)
                cbModel.SelectedIndex = 0;

            if (rbClaudeSub.Checked)
                Log("Personal use only: answers come from the Claude CLI you are "
                  + "signed in to. Anthropic does not permit a distributed product "
                  + "to sign its users in to claude.ai, so do not ship a build with "
                  + "this enabled. Needs the Claude CLI installed and signed in.");
            if (rbLocal.Checked)
                Log("A local model needs Ollama (https://ollama.com). Install it, "
                  + "then run:  ollama pull " + ModelName()
                  + "   The listed models are starting points, not "
                  + "recommendations - none has been playtested here, and only "
                  + "Claude has. A smaller model will be worse at staying in "
                  + "character. It still cannot break your save.");
        }

        // The combo shows a description; the sidecar wants only the tag.
        string ModelName()
        {
            string text = cbModel.Text.Trim();
            int space = text.IndexOf(' ');
            return space > 0 ? text.Substring(0, space) : text;
        }

        void SaveFromUi()
        {
            if (rbNone.Checked) settings.Provider = "mock";
            else if (rbOpenAi.Checked) settings.Provider = "openai";
            else if (rbLocal.Checked) settings.Provider = "local";
            else settings.Provider = "claude";

            settings.UseSubscription = rbClaudeSub.Checked;
            settings.ApiKey = tbKey.Enabled ? tbKey.Text.Trim() : settings.ApiKey;
            settings.Model = cbModel.Enabled ? ModelName() : "";
            settings.Voice = cbVoice.Checked;

            try
            {
                settings.Save();
                Log("Saved to " + Settings.Path);
            }
            catch (Exception ex) { Log("Could not save settings: " + ex.Message); }
        }

        // -----------------------------------------------------------------
        // Checks
        // -----------------------------------------------------------------
        void RefreshChecks()
        {
            lbStatus.Text = "Checking...";
            lbStatus.ForeColor = Color.Gray;

            // Read the one piece of UI state the scan needs here, on the UI
            // thread, so the worker touches no controls at all.
            bool wantVoice = cbVoice != null && cbVoice.Checked;

            Thread scan = new Thread(delegate ()
            {
                List<Check> found = GatherChecks(wantVoice);
                try { BeginInvoke((MethodInvoker)delegate { ShowChecks(found); }); }
                catch (InvalidOperationException) { /* window closed mid-scan */ }
            });
            scan.IsBackground = true;
            scan.Start();
        }

        List<Check> GatherChecks(bool wantVoice)
        {
            List<Check> checks = new List<Check>();

            systemPython = FindPython();
            checks.Add(new Check {
                Name = "Python 3.11+",
                Ok = systemPython != null,
                Detail = systemPython ?? "Not found - install the 64-bit build from python.org",
                Fixable = false });

            bool venv = File.Exists(VenvPython);
            checks.Add(new Check {
                Name = "AI sidecar",
                Ok = venv,
                Detail = venv ? VenvPython : "Not set up yet",
                Fixable = systemPython != null });

            bool sdk = venv && ModulePresent("claude_agent_sdk");
            checks.Add(new Check {
                Name = "Claude SDK",
                Ok = sdk || settings.Provider != "claude",
                Detail = sdk ? "installed"
                            : (settings.Provider == "claude"
                               ? "Needed for the Claude backend"
                               : "not needed for this backend"),
                Fixable = venv });

            bool content = Directory.Exists(ContentDir);
            checks.Add(new Check {
                Name = "Game content",
                Ok = content,
                Detail = content ? ContentDir : "Missing - reinstall the game files",
                Fixable = false });

            bool game = File.Exists(GameExe);
            checks.Add(new Check {
                Name = "The game",
                Ok = game,
                Detail = game ? GameExe : "UrQuanMasters.exe not found beside this launcher",
                Fixable = false });

            if (wantVoice)
            {
                bool voice = venv && ModulePresent("chatterbox");
                checks.Add(new Check {
                    Name = "Voice synthesis",
                    Ok = voice,
                    Detail = voice ? "installed" : "Voice is on but not installed yet",
                    Fixable = venv });
            }

            return checks;
        }

        void ShowChecks(List<Check> checks)
        {
            lvChecks.Items.Clear();
            int bad = 0;
            foreach (Check c in checks)
            {
                ListViewItem item = new ListViewItem(c.Ok ? "OK" : "!");
                item.UseItemStyleForSubItems = false;
                item.ForeColor = c.Ok ? Color.Green : Color.Firebrick;
                item.SubItems.Add(c.Name);
                item.SubItems.Add(c.Detail);
                lvChecks.Items.Add(item);
                if (!c.Ok) bad++;
            }

            lbStatus.Text = bad == 0 ? "Ready to play."
                                     : bad + " thing(s) still needed.";
            lbStatus.ForeColor = bad == 0 ? Color.Green : Color.Firebrick;
        }

        string FindPython()
        {
            foreach (string exe in new string[] { "py", "python" })
            {
                string version = CaptureOutput(exe,
                    "-c \"import sys;print('%d.%d'%sys.version_info[:2])\"");
                if (version == null) continue;
                string[] bits = version.Trim().Split('.');
                int major, minor;
                if (bits.Length == 2 && int.TryParse(bits[0], out major)
                    && int.TryParse(bits[1], out minor)
                    && (major > 3 || (major == 3 && minor >= 11)))
                {
                    return exe + " (" + version.Trim() + ")";
                }
            }
            return null;
        }

        bool ModulePresent(string module)
        {
            return CaptureOutput(VenvPython, "-c \"import " + module + "\"") != null;
        }

        // Runs something and returns stdout, or null if it failed at all.
        string CaptureOutput(string exe, string args)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(exe, args);
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                using (Process p = Process.Start(psi))
                {
                    string output = p.StandardOutput.ReadToEnd();
                    p.StandardError.ReadToEnd();
                    p.WaitForExit(20000);
                    return p.ExitCode == 0 ? output : null;
                }
            }
            catch { return null; }
        }

        // -----------------------------------------------------------------
        // Long jobs, off the UI thread so the window keeps painting
        // -----------------------------------------------------------------
        void StartWork(ThreadStart job)
        {
            SetBusy(true);
            Thread worker = new Thread(delegate ()
            {
                try { job(); }
                catch (Exception ex) { Log("Failed: " + ex.Message); }
                finally
                {
                    BeginInvoke((MethodInvoker)delegate { SetBusy(false); RefreshChecks(); });
                }
            });
            worker.IsBackground = true;
            worker.Start();
        }

        void SetBusy(bool busy)
        {
            btInstall.Enabled = !busy;
            btTest.Enabled = !busy;
            btPlay.Enabled = !busy;
            btRecheck.Enabled = !busy;
            Cursor = busy ? Cursors.WaitCursor : Cursors.Default;
        }

        void InstallMissing()
        {
            if (systemPython == null)
            {
                Log("Install Python 3.11 or newer first: https://www.python.org/downloads/");
                Log("Choose the 64-bit installer and tick \"Add python.exe to PATH\".");
                return;
            }

            string python = systemPython.Split(' ')[0];

            if (!File.Exists(VenvPython))
            {
                Log("Creating the Python environment...");
                Run(python, "-m venv \"" + Path.Combine(AiDir, ".venv") + "\"");
            }
            if (!File.Exists(VenvPython)) { Log("Could not create it."); return; }

            Log("Installing the AI packages...");
            Run(VenvPython, "-m pip install --upgrade pip");
            Run(VenvPython, "-m pip install --upgrade claude-agent-sdk");

            if (cbVoice.Checked && !ModulePresent("chatterbox"))
            {
                Log("Installing voice synthesis. This is several GB and will take a while...");
                Run(VenvPython, "-m pip install --upgrade chatterbox-tts");
            }
            Log("Done.");
        }

        void TestConnection()
        {
            if (!File.Exists(VenvPython))
            {
                Log("Install the AI packages first.");
                return;
            }
            if (settings.Provider == "mock")
            {
                Log("No AI selected - nothing to test.");
                return;
            }
            Log("Asking " + settings.Provider + " for one reply...");
            Run(VenvPython, "-m uqm_ai --provider " + settings.Provider + " --preflight",
                AiDir);
        }

        void Play()
        {
            if (!File.Exists(GameExe))
            {
                MessageBox.Show(this, "UrQuanMasters.exe is not next to this launcher.",
                    "Cannot start", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            string args = "";
            if (settings.Provider == "mock") args += " --no-ai";
            if (settings.Voice) args += " --ai-voice";

            ProcessStartInfo psi = new ProcessStartInfo(GameExe, args.Trim());
            // The game resolves the sidecar as "ai" relative to the executable.
            psi.WorkingDirectory = root;
            psi.UseShellExecute = false;
            try { Process.Start(psi); Close(); }
            catch (Exception ex)
            {
                MessageBox.Show(this, ex.Message, "Could not start the game",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        // Runs a command and streams both streams into the log.
        void Run(string exe, string args, string workingDir = null)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(exe, args);
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                if (workingDir != null) psi.WorkingDirectory = workingDir;

                using (Process p = new Process())
                {
                    p.StartInfo = psi;
                    p.OutputDataReceived += delegate (object s, DataReceivedEventArgs e)
                        { if (e.Data != null) Log(e.Data); };
                    p.ErrorDataReceived += delegate (object s, DataReceivedEventArgs e)
                        { if (e.Data != null) Log(e.Data); };
                    p.Start();
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                    p.WaitForExit();
                }
            }
            catch (Exception ex) { Log("Could not run " + exe + ": " + ex.Message); }
        }

        void Log(string message)
        {
            if (tbLog == null) return;
            if (tbLog.InvokeRequired)
            {
                tbLog.BeginInvoke((MethodInvoker)delegate { Log(message); });
                return;
            }
            tbLog.AppendText(message + Environment.NewLine);
        }
    }
}
