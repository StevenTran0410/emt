# Upstream Grammar Metadata

- **Grammar**: ANTLR grammars-v4 `cobol85`
- **Source**: https://github.com/antlr/grammars-v4/tree/master/cobol85
- **Commit SHA**: `8af0d4c26c796ea27c15c3d85418f2d0f77c3adb`
- **License**: MIT (ProLeap / Ulrich Wolffgang)
- **ANTLR Version**: 4.13.2 (Jar & Python Runtime)
- **Generation Command**:
  ```powershell
  java -jar backend\tools\antlr\antlr-4.13.2-complete.jar -Dlanguage=Python3 -listener -Xexact-output-dir -o backend\domain\structural_graph\_cobol\generated backend\domain\structural_graph\_cobol\grammar\Cobol85Preprocessor.g4 backend\domain\structural_graph\_cobol\grammar\Cobol85.g4
  ```
