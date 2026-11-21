import { execFile } from "node:child_process";
import { mkdir, realpath, stat } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { tool } from "@opencode-ai/plugin";

const run = promisify(execFile);
const inputDirectory = "(?:fixtures/study|flashpoint-exports)";
const fileName = "[A-Za-z0-9][A-Za-z0-9._-]*";
const jsonPath = new RegExp(`^${inputDirectory}/${fileName}\\.json$`);
const instrumentPath = new RegExp(`^(?:${inputDirectory}|build)/${fileName}\\.json$`);
const csvPath = new RegExp(`^${inputDirectory}/${fileName}\\.csv$`);
const recordName = new RegExp(`^${fileName}\\.json$`);

const designArgs = {
  claim: tool.schema.string().describe("Approved claim JSON in fixtures/study/ or flashpoint-exports/"),
  instrument: tool.schema.string().describe("Instrument JSON in fixtures/study/, flashpoint-exports/, or build/"),
};

const inputArgs = {
  ...designArgs,
  responses: tool.schema.string().optional().describe("Normalized response CSV in fixtures/study/ or flashpoint-exports/"),
};

async function inputFile(root, path, pattern, label) {
  if (!pattern.test(path)) {
    throw new Error(`${label} needs a file in the documented study directories.`);
  }
  const absolutePath = join(root, path);
  try {
    if (await realpath(absolutePath) === absolutePath && (await stat(absolutePath)).isFile()) {
      return absolutePath;
    }
  } catch {
    // Use the same diagnostic for missing files and paths the tool cannot read.
  }
  throw new Error(`${label} needs a readable file, with no symbolic links.`);
}

async function outputFile(root, name) {
  if (!recordName.test(name)) {
    throw new Error("Use a new filename ending in .json.");
  }
  const output = join(root, "build");
  await mkdir(output, { recursive: true });
  if (await realpath(output) !== output) {
    throw new Error("Study outputs need a build directory with no symbolic links.");
  }
  return join(output, name);
}

async function studyInputs(root, args) {
  const paths = [
    await inputFile(root, args.claim, jsonPath, "Claim"),
    await inputFile(root, args.instrument, instrumentPath, "Instrument"),
  ];
  if (args.responses !== undefined) {
    paths.push("--responses", await inputFile(root, args.responses, csvPath, "Responses"));
  }
  return paths;
}

async function runVerifier(root, command) {
  try {
    const { stdout } = await run("python3", ["-m", "hunchlab", ...command], {
      cwd: root,
      timeout: 30_000,
    });
    return stdout.trim();
  } catch {
    throw new Error("The verifier could not complete this check. Run the equivalent Python command locally to inspect its diagnostic.");
  }
}

export const verify = tool({
  description: "Check a study against the approved claim. Returns the verdict, evidence, any blockers and repair, and aggregate scoring when a matching export is supplied.",
  args: {
    ...inputArgs,
    save_record: tool.schema.string().optional().describe("New decision record filename ending in .json under build/. Omit to run without saving."),
  },
  async execute(args, context) {
    const root = await realpath(context.worktree);
    const command = ["verify", ...await studyInputs(root, args)];
    if (args.save_record !== undefined) {
      command.push("--save", await outputFile(root, args.save_record));
    }
    return runVerifier(root, command);
  },
});

export const propose = tool({
  description: "Generate a candidate revision for the controlled comparison and optionally save it locally for verification. The result is a proposal, with no changes in Flashpoint.",
  args: {
    ...designArgs,
    revision: tool.schema.string().describe("New revision identifier for the candidate"),
    save_candidate: tool.schema.string().optional().describe("New candidate filename ending in .json under build/. Omit to return the candidate without saving."),
  },
  async execute(args, context) {
    const root = await realpath(context.worktree);
    const command = ["propose", ...await studyInputs(root, args), `--revision=${args.revision}`];
    if (args.save_candidate !== undefined) {
      command.push("--save", await outputFile(root, args.save_candidate));
    }
    return runVerifier(root, command);
  },
});

export const replay = tool({
  description: "Recompute a saved study verdict and check whether the decision record and current inputs agree.",
  args: {
    ...inputArgs,
    record: tool.schema.string().describe("Saved decision record filename ending in .json under build/"),
  },
  async execute(args, context) {
    const root = await realpath(context.worktree);
    if (!recordName.test(args.record)) {
      throw new Error("Use a decision record filename ending in .json.");
    }
    const record = await inputFile(root, `build/${args.record}`, /^build\//, "Decision record");
    return runVerifier(root, ["replay", record, ...await studyInputs(root, args)]);
  },
});
