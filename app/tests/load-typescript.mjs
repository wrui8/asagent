import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { compileFunction } from "node:vm";
import ts from "typescript";

export function compileTestModule(name) {
  const filename = fileURLToPath(new URL(`../src/lib/${name}.ts`, import.meta.url));
  const { outputText } = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  });
  const evaluate = compileFunction(outputText, ["require", "module", "exports"], { filename });
  return (imports = {}) => {
    const module = { exports: {} };
    const require = (specifier) => {
      if (!Object.hasOwn(imports, specifier)) throw new Error(`Unexpected dependency ${specifier}`);
      return imports[specifier];
    };
    evaluate(require, module, module.exports);
    return module.exports;
  };
}
