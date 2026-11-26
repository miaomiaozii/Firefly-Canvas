//go:build windows && amd64

// This lab never provisions a profile or modifies ACLs outside its fresh scratch root.
package main

import (
	"crypto/rand"
	"debug/pe"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"
)

const fakeEnv = "FIREFLY_444_FAKE_CREDENTIAL"
const marker = "Firefly444-synthetic-only"
const packages = "S-1-15-2-1"
const restrictedPackages = "S-1-15-2-2"
const startExtended = 0x80000
const startUnicode = 0x400
const startSuspended = 4
const attrCapabilities = 0x20009
const attrPackagesPolicy = 0x2000f
const attrHandles = 0x20002

var kernel = syscall.NewLazyDLL("kernel32.dll")
var userenv = syscall.NewLazyDLL("userenv.dll")
var advapi = syscall.NewLazyDLL("advapi32.dll")
var initAttrs = kernel.NewProc("InitializeProcThreadAttributeList")
var setAttr = kernel.NewProc("UpdateProcThreadAttribute")
var deleteAttrs = kernel.NewProc("DeleteProcThreadAttributeList")
var deriveSID = userenv.NewProc("DeriveAppContainerSidFromAppContainerName")
var freeSid = advapi.NewProc("FreeSid")
var convertSid = advapi.NewProc("ConvertSidToStringSidW")
var localFree = kernel.NewProc("LocalFree")
var createEvent = kernel.NewProc("CreateEventW")
var openEvent = kernel.NewProc("OpenEventW")
var setHandleInformation = kernel.NewProc("SetHandleInformation")
var getHandleInformation = kernel.NewProc("GetHandleInformation")
var createJob = kernel.NewProc("CreateJobObjectW")
var assignJob = kernel.NewProc("AssignProcessToJobObject")
var terminateJob = kernel.NewProc("TerminateJobObject")
var resumeThread = kernel.NewProc("ResumeThread")

func p(s string) *uint16      { v, e := syscall.UTF16PtrFromString(s); must(e); return v }
func ptr[T any](v *T) uintptr { return uintptr(unsafe.Pointer(v)) }
func must(e error) {
	if e != nil {
		panic(e)
	}
}
func require(op string, ok bool, e error) {
	if !ok {
		panic(fmt.Errorf("%s: %w", op, e))
	}
}
func hexID() string {
	b := make([]byte, 12)
	_, e := rand.Read(b)
	must(e)
	return hex.EncodeToString(b)
}

func getToken(class uint32) (uint32, error) {
	var token syscall.Token
	e := syscall.OpenProcessToken(syscall.Handle(^uintptr(0)), syscall.TOKEN_QUERY, &token)
	if e != nil {
		return 0, e
	}
	defer token.Close()
	var val, size uint32
	e = syscall.GetTokenInformation(token, class, (*byte)(unsafe.Pointer(&val)), 4, &size)
	return val, e
}

type capInfo struct {
	sid, caps       uintptr
	count, reserved uint32
}
type extendedInfo struct {
	base  syscall.StartupInfo
	attrs *byte
}

func spawn(exe, cwd string, args, env []string, sid uintptr, lpac bool, job uintptr, inherited uintptr) (uint32, error) {
	appl, e := syscall.UTF16PtrFromString(exe)
	if e != nil {
		return 0, e
	}
	cmd := syscall.EscapeArg(exe)
	for _, a := range args {
		cmd += " " + syscall.EscapeArg(a)
	}
	line, e := syscall.UTF16PtrFromString(cmd)
	if e != nil {
		return 0, e
	}
	var dir *uint16
	if cwd != "" {
		dir = p(cwd)
	}
	envBlock := []uint16{}
	for _, kv := range env {
		x, err := syscall.UTF16FromString(kv)
		if err != nil {
			return 0, err
		}
		envBlock = append(envBlock, x...)
	}
	envBlock = append(envBlock, 0)
	var si extendedInfo
	si.base.Cb = uint32(unsafe.Sizeof(si.base))
	flags := uint32(startUnicode)
	if job != 0 {
		flags |= startSuspended
	}
	n := uintptr(0)
	if sid != 0 {
		n++
	}
	if lpac {
		n++
	}
	if inherited != 0 {
		n++
	}
	if n > 0 {
		var size uintptr
		_, _, e := initAttrs.Call(0, n, 0, uintptr(unsafe.Pointer(&size)))
		if size == 0 {
			return 0, fmt.Errorf("attribute sizing: %w", e)
		}
		attr := make([]byte, size)
		capabilities := &capInfo{sid: sid}
		policy := new(uint32)
		*policy = 1
		handleValue := new(uintptr)
		*handleValue = inherited
		var pins runtime.Pinner
		pins.Pin(&attr[0])
		pins.Pin(capabilities)
		pins.Pin(policy)
		pins.Pin(handleValue)
		defer pins.Unpin()
		si.attrs = &attr[0]
		si.base.Cb = uint32(unsafe.Sizeof(si))
		flags |= startExtended
		r, _, e := initAttrs.Call(uintptr(unsafe.Pointer(si.attrs)), n, 0, uintptr(unsafe.Pointer(&size)))
		if r == 0 {
			return 0, fmt.Errorf("initialize attributes: %w", e)
		}
		defer deleteAttrs.Call(uintptr(unsafe.Pointer(si.attrs)))
		set := func(key, value, size uintptr) error {
			r, _, e := setAttr.Call(uintptr(unsafe.Pointer(si.attrs)), 0, key, value, size, 0, 0)
			if r == 0 {
				return fmt.Errorf("set attribute %#x: %w", key, e)
			}
			return nil
		}
		if sid != 0 {
			if e := set(attrCapabilities, ptr(capabilities), unsafe.Sizeof(*capabilities)); e != nil {
				return 0, e
			}
		}
		if lpac {
			if e := set(attrPackagesPolicy, ptr(policy), unsafe.Sizeof(*policy)); e != nil {
				return 0, e
			}
		}
		if inherited != 0 {
			if e := set(attrHandles, ptr(handleValue), unsafe.Sizeof(*handleValue)); e != nil {
				return 0, e
			}
		}
	}
	var pi syscall.ProcessInformation
	e = syscall.CreateProcess(appl, line, nil, nil, inherited != 0, flags, &envBlock[0], dir, &si.base, &pi)
	if e != nil {
		return 0, fmt.Errorf("CreateProcess (%s): %w", status(e), e)
	}
	defer syscall.CloseHandle(pi.Process)
	defer syscall.CloseHandle(pi.Thread)
	if job != 0 {
		r, _, e := assignJob.Call(job, uintptr(pi.Process))
		if r == 0 {
			_ = syscall.TerminateProcess(pi.Process, 2)
			return 0, fmt.Errorf("assign job: %w", e)
		}
		r, _, e = resumeThread.Call(uintptr(pi.Thread))
		if uint32(r) == 0xffffffff {
			_ = syscall.TerminateProcess(pi.Process, 2)
			return 0, fmt.Errorf("resume: %w", e)
		}
	}
	r, e := syscall.WaitForSingleObject(pi.Process, 20000)
	if e != nil || r != 0 {
		_ = syscall.TerminateProcess(pi.Process, 2)
		return 0, fmt.Errorf("process wait: result=%d err=%v", r, e)
	}
	var exit uint32
	e = syscall.GetExitCodeProcess(pi.Process, &exit)
	return exit, e
}

func status(e error) string {
	if e == nil {
		return "OK"
	}
	var n syscall.Errno
	if errors.As(e, &n) {
		return "WIN32_" + strconv.Itoa(int(n))
	}
	return "ERROR: " + e.Error()
}
func readStatus(path string) string {
	b, e := os.ReadFile(path)
	if e != nil {
		return status(e)
	}
	if string(b) != marker {
		return "UNEXPECTED_CONTENT"
	}
	return "MARKER"
}
func listStatus(path string) string {
	xs, e := os.ReadDir(path)
	if e != nil {
		return status(e)
	}
	for _, x := range xs {
		if x.Name() == "canary.txt" {
			return "LISTED"
		}
	}
	return "NOT_LISTED"
}
func getEventStatus(name string) string {
	arg := p(name)
	r, _, e := openEvent.Call(0x100000, 0, uintptr(unsafe.Pointer(arg)))
	if r == 0 {
		return status(e)
	}
	defer syscall.CloseHandle(syscall.Handle(r))
	w, e := syscall.WaitForSingleObject(syscall.Handle(r), 0)
	if w == 0 && e == nil {
		return "SIGNALED"
	}
	return fmt.Sprintf("WAIT_%d_%s", w, status(e))
}
func handleStatus(h uintptr, knownSynthetic bool) string {
	var flags uint32
	r, _, e := getHandleInformation.Call(h, uintptr(unsafe.Pointer(&flags)))
	if r == 0 {
		return status(e)
	}
	// An unrelated handle can reuse the number; only the explicit host control may read it.
	if !knownSynthetic {
		return "VALUE_REUSED_UNVERIFIED"
	}
	typ, e := syscall.GetFileType(syscall.Handle(h))
	if e != nil || typ != syscall.FILE_TYPE_DISK {
		return fmt.Sprintf("NOT_DISK_%d_%s", typ, status(e))
	}
	var data [128]byte
	var n uint32
	e = syscall.ReadFile(syscall.Handle(h), data[:], &n, nil)
	if e != nil {
		return status(e)
	}
	if string(data[:n]) == marker {
		return "MARKER"
	}
	return "UNEXPECTED_CONTENT"
}
func bridgeStatus(addr, route string) string {
	c := http.Client{Timeout: 2 * time.Second, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	defer c.CloseIdleConnections()
	r, e := c.Get("http://" + addr + route)
	if e != nil {
		return status(e)
	}
	defer r.Body.Close()
	data, e := io.ReadAll(io.LimitReader(r.Body, 128))
	if e != nil {
		return status(e)
	}
	if string(data) == marker {
		return fmt.Sprintf("HTTP_%d_MARKER", r.StatusCode)
	}
	return fmt.Sprintf("HTTP_%d_NO_MARKER", r.StatusCode)
}

type result struct {
	Mode                             string
	Depth                            int
	Container, LPAC, CapabilityCount uint32
	Checks                           map[string]string
	Next                             *uint32 `json:",omitempty"`
	SpawnError                       string  `json:",omitempty"`
}

func probe(a []string) {
	root, mode, event, addr := a[0], a[1], a[2], a[3]
	depth, e := strconv.Atoi(a[4])
	must(e)
	h, e := strconv.ParseUint(a[5], 10, 64)
	must(e)
	workspace := filepath.Join(root, "workspace")
	ac, e := getToken(29)
	must(e)
	lpac := uint32(0)
	if ac != 0 {
		lpac, e = getToken(46)
		must(e)
	}
	var token syscall.Token
	must(syscall.OpenProcessToken(syscall.Handle(^uintptr(0)), syscall.TOKEN_QUERY, &token))
	defer token.Close()
	capCount := uint32(0)
	if ac != 0 {
		var capData [1024]byte
		var n uint32
		must(syscall.GetTokenInformation(token, 30, &capData[0], uint32(len(capData)), &n))
		capCount = *(*uint32)(unsafe.Pointer(&capData[0]))
	}
	r := result{Mode: mode, Depth: depth, Container: ac, LPAC: lpac, CapabilityCount: capCount, Checks: map[string]string{}}
	for _, bucket := range []string{"workspace", "sibling", "fake-home", "all-packages"} {
		d := filepath.Join(root, bucket)
		r.Checks[bucket+"_read"] = readStatus(filepath.Join(d, "canary.txt"))
		r.Checks[bucket+"_list"] = listStatus(d)
	}
	r.Checks["junction_read"] = readStatus(filepath.Join(workspace, "to-sibling", "canary.txt"))
	r.Checks["junction_list"] = listStatus(filepath.Join(workspace, "to-sibling"))
	r.Checks["hardlink_read"] = readStatus(filepath.Join(workspace, "linked-canary.txt"))
	r.Checks["credential_env"] = "ABSENT"
	if key, ok := os.LookupEnv(fakeEnv); ok {
		r.Checks["credential_env"] = "UNEXPECTED_CONTENT"
		if key == marker {
			r.Checks["credential_env"] = "MARKER"
		}
	}
	r.Checks["credential_handle"] = handleStatus(uintptr(h), mode == "host" && depth == 0)
	r.Checks["host_event"] = getEventStatus(event)
	r.Checks["bridge_allowed"] = bridgeStatus(addr, "/attachment/allowed")
	r.Checks["bridge_denied"] = bridgeStatus(addr, "/attachment/denied")
	if depth < 2 {
		exe, e := os.Executable()
		must(e)
		next := append([]string{"probe"}, a...)
		next[5] = strconv.Itoa(depth + 1)
		exit, e := spawn(exe, workspace, next, os.Environ(), 0, false, 0, 0)
		if e != nil {
			r.SpawnError = e.Error()
		} else {
			r.Next = &exit
		}
	}
	out, e := json.Marshal(r)
	must(e)
	fmt.Println(string(out))
	must(os.WriteFile(filepath.Join(workspace, fmt.Sprintf("%s-%d.json", mode, depth)), out, 0600))
}

func icacls(path string, args ...string) {
	exe := filepath.Join(os.Getenv("SystemRoot"), "System32", "icacls.exe")
	cmd := exec.Command(exe, append([]string{path}, args...)...)
	if b, e := cmd.CombinedOutput(); e != nil {
		panic(fmt.Errorf("icacls %s: %w %q", strings.Join(args, " "), e, b))
	}
}

func imageImports(exe string) ([]string, error) {
	file, err := pe.Open(exe)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	symbols, err := file.ImportedSymbols()
	if err != nil {
		return nil, err
	}
	var names []string
	seen := map[string]bool{}
	for _, v := range symbols {
		_, dll, ok := strings.Cut(v, ":")
		if !ok {
			return nil, fmt.Errorf("unrecognized PE import")
		}
		dll = strings.ToLower(dll)
		if !seen[dll] {
			seen[dll] = true
			names = append(names, dll)
		}
	}
	return names, nil
}

func run(root string) bool {
	root, e := filepath.Abs(root)
	must(e)
	if filepath.Base(root) != "synthetic" {
		panic("lab root must be a new directory named synthetic")
	}
	must(os.Mkdir(root, 0700))
	defer func() { must(os.RemoveAll(root)) }()
	workspace := filepath.Join(root, "workspace")
	for _, name := range []string{"workspace", "sibling", "fake-home", "all-packages"} {
		d := filepath.Join(root, name)
		must(os.Mkdir(d, 0700))
		must(os.WriteFile(filepath.Join(d, "canary.txt"), []byte(marker), 0600))
	}
	sidName := p("Firefly444-" + hexID())
	var sid uintptr
	hr, _, _ := deriveSID.Call(uintptr(unsafe.Pointer(sidName)), uintptr(unsafe.Pointer(&sid)))
	require("derive SID without profile", uint32(hr) == 0 && sid != 0, fmt.Errorf("HRESULT=%#x", uint32(hr)))
	defer freeSid.Call(sid)
	var sidString *uint16
	r, _, e := convertSid.Call(sid, uintptr(unsafe.Pointer(&sidString)))
	require("convert SID", r != 0, e)
	defer localFree.Call(ptr(sidString))
	var sidChars []uint16
	for i := 0; ; i++ {
		v := *(*uint16)(unsafe.Add(unsafe.Pointer(sidString), i*2))
		if v == 0 {
			break
		}
		sidChars = append(sidChars, v)
	}
	sidText := syscall.UTF16ToString(sidChars)
	var launcherToken syscall.Token
	must(syscall.OpenProcessToken(syscall.Handle(^uintptr(0)), syscall.TOKEN_QUERY, &launcherToken))
	defer launcherToken.Close()
	launcherUser, e := launcherToken.GetTokenUser()
	must(e)
	launcherSID, e := launcherUser.User.Sid.String()
	must(e)
	cmd := exec.Command(filepath.Join(os.Getenv("SystemRoot"), "System32", "cmd.exe"), "/d", "/c", "mklink", "/J", filepath.Join(workspace, "to-sibling"), filepath.Join(root, "sibling"))
	if b, e := cmd.CombinedOutput(); e != nil {
		panic(fmt.Errorf("junction: %w %q", e, b))
	}
	j := filepath.Join(workspace, "to-sibling")
	defer func() { must(os.Remove(j)) }()
	must(os.Link(filepath.Join(root, "sibling", "canary.txt"), filepath.Join(workspace, "linked-canary.txt")))
	exe, e := os.Executable()
	must(e)
	st, e := os.ReadFile(exe)
	must(e)
	exe = filepath.Join(workspace, "probe.exe")
	must(os.WriteFile(exe, st, 0700))
	imports, e := imageImports(exe)
	must(e)
	fmt.Printf("probe_static_imports=%s\n", strings.Join(imports, ","))
	h, e := syscall.CreateFile(p(filepath.Join(root, "fake-home", "canary.txt")), syscall.GENERIC_READ, syscall.FILE_SHARE_READ, nil, syscall.OPEN_EXISTING, 0, 0)
	must(e)
	defer syscall.CloseHandle(h)
	r, _, e = setHandleInformation.Call(uintptr(h), 1, 1)
	require("make fake handle inheritable", r != 0, e)
	event := "Local\\Firefly444-" + hexID()
	eventName := p(event)
	ev, _, e := createEvent.Call(0, 1, 1, uintptr(unsafe.Pointer(eventName)))
	require("create host-only event", ev != 0, e)
	defer syscall.CloseHandle(syscall.Handle(ev))
	l, e := net.Listen("tcp4", "127.0.0.1:0")
	must(e)
	defer l.Close()
	var allowed, denied atomic.Int32
	server := &http.Server{ReadHeaderTimeout: time.Second, Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/attachment/allowed":
			allowed.Add(1)
			_, _ = io.WriteString(w, marker)
		case "/attachment/denied":
			denied.Add(1)
			w.WriteHeader(403)
		default:
			w.WriteHeader(404)
		}
	})}
	go server.Serve(l)
	defer server.Close()
	incomplete := false
	variants := []struct {
		mode                     string
		host, lpac, inheritedCwd bool
	}{
		{mode: "host", host: true}, {mode: "host-clean", host: true},
		{mode: "appcontainer"}, {mode: "lpac", lpac: true},
		{mode: "appcontainer-group-rx"}, {mode: "lpac-group-rx", lpac: true},
		{mode: "appcontainer-inherited-cwd", inheritedCwd: true}, {mode: "lpac-inherited-cwd", lpac: true, inheritedCwd: true},
	}
	for _, variant := range variants {
		mode := variant.mode
		if mode == "appcontainer" {
			icacls(root, "/grant", "*"+sidText+":(RX)")
			// Inherited ACL or integrity rules here could change the object behind the hardlink.
			icacls(workspace, "/grant", "*"+sidText+":(M)", "*"+launcherSID+":(M)")
			icacls(workspace, "/setintegritylevel", "L")
			for _, path := range []string{exe, filepath.Join(workspace, "canary.txt")} {
				icacls(path, "/grant", "*"+sidText+":(RX)")
			}
			for _, path := range []string{filepath.Join(root, "all-packages"), filepath.Join(root, "all-packages", "canary.txt")} {
				icacls(path, "/grant", "*"+packages+":(RX)")
			}
		}
		if mode == "appcontainer-group-rx" {
			for _, path := range []string{root, workspace, exe} {
				icacls(path, "/grant", "*"+packages+":(RX)", "*"+restrictedPackages+":(RX)")
			}
			fmt.Println("group_rx=synthetic-root,workspace,probe.exe")
		}
		if _, e := os.Stat(exe); e != nil {
			panic(fmt.Errorf("supervisor executable stat: %w", e))
		}
		job, _, e := createJob.Call(0, 0)
		require("launch-owned job", job != 0, e)
		func() {
			defer syscall.CloseHandle(syscall.Handle(job))
			defer func() { r, _, e := terminateJob.Call(job, 1); require("owned job cleanup", r != 0, e) }()
			beforeA, beforeD := allowed.Load(), denied.Load()
			for depth := 0; depth <= 2; depth++ {
				f := filepath.Join(workspace, fmt.Sprintf("%s-%d.json", mode, depth))
				must(os.WriteFile(f, []byte("NOT_RUN"), 0600))
				if !variant.host {
					icacls(f, "/grant", "*"+sidText+":(M)", "*"+launcherSID+":(M)")
					icacls(f, "/setintegritylevel", "L")
				}
			}
			env := []string{"LOCALAPPDATA=" + workspace, "SystemRoot=" + os.Getenv("SystemRoot"), "TEMP=" + workspace, "TMP=" + workspace, "USERPROFILE=" + workspace, "windir=" + os.Getenv("SystemRoot")}
			pkg := sid
			inherited := uintptr(0)
			if variant.host {
				pkg = 0
			}
			if mode == "host" {
				env = append([]string{fakeEnv + "=" + marker}, env...)
				inherited = uintptr(h)
			}
			cwd := workspace
			if variant.inheritedCwd {
				previous, e := os.Getwd()
				must(e)
				must(os.Chdir(workspace))
				defer func() { must(os.Chdir(previous)) }()
				cwd = ""
			}
			exit, err := spawn(exe, cwd, []string{"probe", root, mode, event, l.Addr().String(), "0", strconv.Itoa(int(h))}, env, pkg, variant.lpac, job, inherited)
			exitText := "NOT_OBSERVED"
			if err == nil {
				exitText = fmt.Sprintf("%#x", exit)
			}
			fmt.Printf("mode=%s exit=%s spawn_error=%v bridge_allowed_hits=%d bridge_denied_hits=%d\n", mode, exitText, err, allowed.Load()-beforeA, denied.Load()-beforeD)
			if exit != 0 || err != nil {
				incomplete = true
			}
			for depth := 0; depth <= 2; depth++ {
				f := filepath.Join(workspace, fmt.Sprintf("%s-%d.json", mode, depth))
				b, e := os.ReadFile(f)
				if e == nil && string(b) != "NOT_RUN" {
					fmt.Println("probe_report " + string(b))
				} else {
					incomplete = true
					fmt.Printf("mode=%s depth=%d report_not_observed=%s\n", mode, depth, status(e))
				}
			}
		}()
	}
	if incomplete {
		fmt.Println("required probe result missing; no isolation verdict")
	}
	return !incomplete
}

func main() {
	if len(os.Args) == 8 && os.Args[1] == "probe" {
		probe(os.Args[2:])
		return
	}
	if len(os.Args) != 2 {
		panic("only a fresh synthetic lab root is accepted")
	}
	if !run(os.Args[1]) {
		os.Exit(2)
	}
}
