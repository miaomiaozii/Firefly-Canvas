import {
    createReadToolDefinition, createWriteToolDefinition, createEditToolDefinition, VERSION,
    type ExtensionAPI, type ToolDefinition,
} from "pi-stable";
import { lstat, realpath, mkdir, writeFile, readFile, access } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";

const windows = process.platform === "win32";
const denied = (reason: string): never => { throw new Error(`[session-file-tools] Access denied: ${reason}`); };
const comparison = (value: string) => windows ? value.toLowerCase() : value;

// #445: reject ambiguous Win32/SDK aliases before normalization can erase their spelling.
function checkedSpelling(input: unknown): string {
    if (typeof input !== "string" || !input || /[\x00-\x1f<>"|?*~\u00a0\u2000-\u200a\u202f\u205f\u3000]/u.test(input)) {
        denied("unsupported path spelling");
    }
    const value = input as string;
    if (/^[\\/]{2}/.test(value) || (windows && /^[\\/]/.test(value))) denied("UNC/device/root-relative paths are unavailable");
    const drive = /^[a-z]:[\\/]/i.test(value);
    const rest = drive ? value.slice(3) : value;
    if (rest.includes(":")) denied("drive-relative paths and alternate streams are unavailable");
    for (const part of rest.split(/[\\/]/)) {
        if (part === ".." || /[. ]$/.test(part) && part !== ".") denied("traversal or trailing-dot/space alias");
        if (/^(con|prn|aux|nul|clock\$|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:[ .]|$)/iu.test(part)) denied("reserved device name");
    }
    if (!windows && (drive || value.includes("\\"))) denied("non-native path");
    return value;
}

async function inspect(absolute: string, allowMissing: boolean, directory = false) {
    const base = path.parse(absolute).root;
    const components = absolute.slice(base.length).split(path.sep).filter(Boolean);
    let current = base;
    for (let index = -1; index < components.length; index++) {
        if (index >= 0) current = path.join(current, components[index]);
        let stats;
        try { stats = await lstat(current); }
        catch (error: any) {
            if (allowMissing && error.code === "ENOENT") return;
            throw error;
        }
        if (stats.isSymbolicLink()) denied("links/junctions are unavailable");
        // A realpath mismatch also rejects short names and redirected components not exposed as symlinks.
        if (comparison(path.resolve(await realpath(current))) !== comparison(path.resolve(current))) denied("redirected or aliased path component");
        if (index < components.length - 1 || directory) {
            if (!stats.isDirectory()) denied("a parent is not a directory");
        } else {
            if (!stats.isFile()) denied("only regular files are available");
            if (stats.nlink !== 1) denied("shared-inode files are unavailable");
        }
    }
}

export function registerSessionFileTools(pi: ExtensionAPI) {
    const configuredRoot = process.env.FIREFLY_AGENT_WORKSPACE_ROOT;
    if (!configuredRoot) return;
    // #445: pin the reviewed path/media/operations contract; never downgrade to bare tools.
    if (VERSION !== "1.5.1" || [createReadToolDefinition, createWriteToolDefinition, createEditToolDefinition]
        .some(factory => typeof factory !== "function")) {
        throw new Error("[session-file-tools] Unavailable: incompatible Pi SDK (requires 1.5.1)");
    }
    const root = path.resolve(configuredRoot);
    async function target(input: unknown, mutation = false, directory = false) {
        checkedSpelling(configuredRoot);
        if (!path.isAbsolute(configuredRoot) || path.basename(root).toLowerCase() !== "sessions") denied("invalid server workspace root");
        try { await inspect(root, false, true); }
        catch { denied("server workspace root is missing, redirected or inaccessible"); }
        const spelling = checkedSpelling(typeof input === "string" && input.startsWith("@") ? input.slice(1) : input);
        const absolute = path.resolve(root, spelling);
        const relative = path.relative(root, absolute);
        if ((!relative && !directory) || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) denied("outside the fixed sessions workspace");
        if (mutation && !relative.includes(path.sep) && /^(?:system-prompt\.txt|.*\.jsonl)$/i.test(relative)) denied("host control file is read-only");
        await inspect(absolute, mutation, directory);
        return absolute;
    }
    const writeOperations = {
        async mkdir(dir: string) {
            const absolute = await target(dir, true, true);
            let current = root;
            // No recursive mkdir: validate the entire requested path first, then each component before creating it.
            for (const part of path.relative(root, absolute).split(path.sep).filter(Boolean)) {
                current = path.join(current, part);
                await target(current, true, true);
                try { await mkdir(current); }
                catch (error: any) { if (error.code !== "EEXIST") throw error; }
                await inspect(current, false, true);
            }
        },
        async writeFile(file: string, content: string) {
            await writeFile(await target(file, true), content, "utf8");
        },
    };
    const editOperations = {
        readFile: async (file: string) => readFile(await target(file, true)),
        writeFile: writeOperations.writeFile,
        access: async (file: string) => access(await target(file, true), constants.R_OK | constants.W_OK),
    };
    const tools: ToolDefinition<any, any, any>[] = [
        createReadToolDefinition(root),
        createWriteToolDefinition(root, { operations: writeOperations }),
        createEditToolDefinition(root, { operations: editOperations }),
    ];
    for (const tool of tools) {
        pi.registerTool({
            ...tool,
            async execute(id, params, signal, onUpdate, ctx) {
                const absolute = await target(params.path, tool.name !== "read");
                // Operations are replacement objects, not merged with SDK defaults. Read keeps the complete
                // native media/streaming backend behind preflight; SDK 1.5.1 only probes fallback spellings
                // for missing files, whereas read preflight requires an existing checked absolute regular file.
                // This rejects stable links, NOT hostile concurrent reparse swaps or other OS actors.
                return tool.execute(id, { ...params, path: absolute }, signal, onUpdate, { ...ctx, cwd: root });
            },
        });
    }
}
