using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using Microsoft.Win32.SafeHandles;

namespace FireflyDesktop
{
    public sealed class FireflyDesktopProcess : IDisposable
    {
        private const string Arguments = @"-u tools\run-server.py --desktop";
        private const int ExitWaitMilliseconds = 2000;
        private const int LogDrainMilliseconds = 2000;
        private const uint KillOnJobClose = 0x00002000;
        private const uint CreateSuspended = 0x00000004;
        private const uint CreateUnicodeEnvironment = 0x00000400;
        private const uint ExtendedStartupInfoPresent = 0x00080000;
        private const uint CreateNoWindow = 0x08000000;
        private const uint StartfUseStdHandles = 0x00000100;
        private const uint HandleFlagInherit = 1;
        private const uint WaitObject0 = 0;
        private const uint WaitTimeout = 258;
        private static readonly IntPtr HandleListAttribute = new IntPtr(0x00020002);

        private readonly object gate = new object();
        private readonly int id;
        private KernelHandle job;
        private KernelHandle process;
        private FileStream input;
        private FileStream outputPipe;
        private FileStream errorPipe;
        private FileStream outputLog;
        private FileStream errorLog;
        private Task outputCopy;
        private Task errorCopy;
        private bool disposed;

        public FireflyDesktopProcess(string python, string root, string stdout, string stderr, string readyPath)
        {
            string executable = Path.GetFullPath(python);
            string workingDirectory = Path.GetFullPath(root);
            if (String.IsNullOrWhiteSpace(readyPath) || readyPath.IndexOf('\0') >= 0)
                throw new ArgumentException("Expected a ready file path.", "readyPath");

            bool assigned = false;
            try
            {
                job = CreateJobObjectW(IntPtr.Zero, null);
                if (job.IsInvalid)
                    throw NativeError("CreateJobObjectW");
                var limits = new JobExtendedLimits();
                // Do not set either breakaway flag: sidecars must stay in this unnamed, non-inheritable job.
                limits.Basic.LimitFlags = KillOnJobClose;
                if (!SetInformationJobObject(job, 9, ref limits, Marshal.SizeOf(typeof(JobExtendedLimits))))
                    throw NativeError("SetInformationJobObject");

                outputLog = OpenLog(stdout);
                errorLog = OpenLog(stderr);
                process = new KernelHandle();
                using (var thread = new KernelHandle())
                {
                    id = StartSuspended(executable, workingDirectory, ChildEnvironment(readyPath), thread);
                    if (!AssignProcessToJobObject(job, process))
                        throw NativeError("AssignProcessToJobObject");
                    assigned = true;

                    outputCopy = CopyLogAsync(outputPipe, outputLog);
                    errorCopy = CopyLogAsync(errorPipe, errorLog);
                    byte[] start = Encoding.ASCII.GetBytes("start\n");
                    input.Write(start, 0, start.Length);
                    input.Flush();
                    if (ResumeThread(thread) == UInt32.MaxValue)
                        throw NativeError("ResumeThread");
                }
            }
            catch
            {
                // Until assignment succeeds, the suspended child is ours alone and cannot have descendants.
                if (!assigned && process != null && !process.IsInvalid)
                    TerminateProcess(process, 1);
                Dispose();
                throw;
            }
        }

        public int Id { get { return id; } }

        public bool HasExited
        {
            get
            {
                lock (gate)
                {
                    if (disposed)
                        return true;
                    uint result = WaitForSingleObject(process, 0);
                    if (result == WaitObject0)
                        return true;
                    if (result == WaitTimeout)
                        return false;
                    throw NativeError("WaitForSingleObject");
                }
            }
        }

        public void RequestStop()
        {
            lock (gate)
                CloseInput();
        }

        public void Dispose()
        {
            lock (gate)
            {
                if (disposed)
                    return;
                disposed = true;
                CloseInput();
                // Closing the last job handle also ends descendants that may still own the log writers.
                if (job != null)
                    job.Dispose();
                try
                {
                    if (process != null && !process.IsInvalid)
                        WaitForSingleObject(process, ExitWaitMilliseconds);
                    Task drain = outputCopy == null ? errorCopy : errorCopy == null ? outputCopy :
                        Task.WhenAll(outputCopy, errorCopy);
                    if (drain != null)
                    {
                        try { drain.Wait(LogDrainMilliseconds); }
                        catch (AggregateException) { } // Keep partial logs if a pipe or log write failed.
                    }
                }
                finally
                {
                    CloseStream(outputPipe);
                    CloseStream(errorPipe);
                    CloseStream(outputLog);
                    CloseStream(errorLog);
                    if (process != null)
                        process.Dispose();
                }
            }
        }

        private void CloseInput()
        {
            CloseStream(input);
            input = null;
        }

        private static void CloseStream(Stream stream)
        {
            if (stream == null)
                return;
            try { stream.Dispose(); }
            catch (IOException) { } // Cleanup must not hide the startup error or strand other handles.
        }

        private static FileStream OpenLog(string path)
        {
            // Avoid buffering short boot messages in the parent so they can be tailed immediately.
            return new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read, 1, FileOptions.Asynchronous);
        }

        private static async Task CopyLogAsync(Stream source, Stream destination)
        {
            // If the destination fails, close the reader so the child cannot block on a full pipe.
            using (source)
                await source.CopyToAsync(destination).ConfigureAwait(false);
        }

        private static byte[] ChildEnvironment(string readyPath)
        {
            var variables = new SortedDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables())
                variables[(string)entry.Key] = (string)entry.Value;
            variables["PYTHONUTF8"] = "1";
            variables["PYTHONIOENCODING"] = "utf-8";
            variables["FIREFLY_BOOT_REDIRECT"] = "1";
            variables["FIREFLY_DESKTOP_READY_FILE"] = readyPath;

            var block = new StringBuilder();
            foreach (var entry in variables)
                block.Append(entry.Key).Append('=').Append(entry.Value).Append('\0');
            return Encoding.Unicode.GetBytes(block.Append('\0').ToString());
        }

        private int StartSuspended(string executable, string root, byte[] environment, KernelHandle thread)
        {
            SafeFileHandle childIn = null, childOut = null, childErr = null;
            IntPtr list = IntPtr.Zero, handles = IntPtr.Zero;
            bool listInitialized = false;
            try
            {
                input = OpenPipe(true, out childIn);
                outputPipe = OpenPipe(false, out childOut);
                errorPipe = OpenPipe(false, out childErr);

                // Only the three child pipe ends may be inherited, not the tray's other handles.
                IntPtr size = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
                if (size == IntPtr.Zero)
                    throw NativeError("InitializeProcThreadAttributeList (size)");
                list = Marshal.AllocHGlobal(size);
                if (!InitializeProcThreadAttributeList(list, 1, 0, ref size))
                    throw NativeError("InitializeProcThreadAttributeList");
                listInitialized = true;
                int handlesSize = 3 * IntPtr.Size;
                handles = Marshal.AllocHGlobal(handlesSize);
                Marshal.WriteIntPtr(handles, 0, childIn.DangerousGetHandle());
                Marshal.WriteIntPtr(handles, IntPtr.Size, childOut.DangerousGetHandle());
                Marshal.WriteIntPtr(handles, 2 * IntPtr.Size, childErr.DangerousGetHandle());
                if (!UpdateProcThreadAttribute(list, 0, HandleListAttribute, handles,
                    new IntPtr(handlesSize), IntPtr.Zero, IntPtr.Zero))
                    throw NativeError("UpdateProcThreadAttribute");

                var startup = new StartupInfoEx();
                startup.Info.Size = Marshal.SizeOf(typeof(StartupInfoEx));
                startup.Info.Flags = StartfUseStdHandles;
                startup.Info.StdInput = childIn.DangerousGetHandle();
                startup.Info.StdOutput = childOut.DangerousGetHandle();
                startup.Info.StdError = childErr.DangerousGetHandle();
                startup.AttributeList = list;

                // CreateProcessW is the no-shell path. Raw pipes avoid Framework Process's stdin BOM;
                // suspension also covers Python launchers that spawn before the script reads its gate.
                var commandLine = new StringBuilder("\"" + executable + "\" " + Arguments);
                ProcessInformation launched;
                if (!CreateProcessW(executable, commandLine, IntPtr.Zero, IntPtr.Zero, true,
                    CreateNoWindow | CreateSuspended | CreateUnicodeEnvironment | ExtendedStartupInfoPresent,
                    environment, root, ref startup, out launched))
                    throw NativeError("CreateProcessW");
                process.TakeOwnership(launched.Process);
                thread.TakeOwnership(launched.Thread);
                return launched.ProcessId;
            }
            finally
            {
                if (listInitialized)
                    DeleteProcThreadAttributeList(list);
                Marshal.FreeHGlobal(list);
                Marshal.FreeHGlobal(handles);
                if (childIn != null) childIn.Dispose();
                if (childOut != null) childOut.Dispose();
                if (childErr != null) childErr.Dispose();
            }
        }

        private static FileStream OpenPipe(bool parentWrites, out SafeFileHandle child)
        {
            SafeFileHandle parent = null;
            child = null;
            try
            {
                bool created = parentWrites ? CreatePipe(out child, out parent, IntPtr.Zero, 0) :
                    CreatePipe(out parent, out child, IntPtr.Zero, 0);
                if (!created)
                    throw NativeError("CreatePipe");
                if (!SetHandleInformation(child, HandleFlagInherit, HandleFlagInherit))
                    throw NativeError("SetHandleInformation");
                return new FileStream(parent, parentWrites ? FileAccess.Write : FileAccess.Read, 4096, false);
            }
            catch
            {
                if (parent != null) parent.Dispose();
                throw;
            }
        }

        private static Win32Exception NativeError(string operation)
        {
            int code = Marshal.GetLastWin32Error();
            return new Win32Exception(code, operation + ": " + new Win32Exception(code).Message);
        }

        private sealed class KernelHandle : SafeHandleZeroOrMinusOneIsInvalid
        {
            public KernelHandle() : base(true) { }
            public void TakeOwnership(IntPtr value) { SetHandle(value); }
            protected override bool ReleaseHandle() { return CloseHandle(handle); }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JobBasicLimits
        {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters
        {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JobExtendedLimits
        {
            public JobBasicLimits Basic;
            public IoCounters Io;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct StartupInfo
        {
            public int Size;
            public IntPtr Reserved, Desktop, Title;
            public uint X, Y, XSize, YSize, XCountChars, YCountChars, FillAttribute, Flags;
            public ushort ShowWindow, ReservedSize;
            public IntPtr ReservedBytes, StdInput, StdOutput, StdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct StartupInfoEx
        {
            public StartupInfo Info;
            public IntPtr AttributeList;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInformation
        {
            public IntPtr Process, Thread;
            public int ProcessId, ThreadId;
        }

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern KernelHandle CreateJobObjectW(IntPtr attributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(KernelHandle job, int infoClass,
            ref JobExtendedLimits info, int size);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(KernelHandle job, KernelHandle process);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CreatePipe(out SafeFileHandle read, out SafeFileHandle write,
            IntPtr attributes, uint size);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetHandleInformation(SafeFileHandle handle, uint mask, uint flags);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute,
            IntPtr value, IntPtr size, IntPtr previousValue, IntPtr returnSize);

        [DllImport("kernel32.dll")]
        private static extern void DeleteProcThreadAttributeList(IntPtr list);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern bool CreateProcessW(string application, StringBuilder commandLine,
            IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint flags,
            byte[] environment, string workingDirectory, ref StartupInfoEx startup, out ProcessInformation process);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(KernelHandle thread);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(KernelHandle process, uint exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(KernelHandle handle, uint milliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);
    }
}
