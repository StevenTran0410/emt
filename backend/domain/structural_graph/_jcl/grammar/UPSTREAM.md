# Upstream ANTLR JCL Grammar

- **Source Repository**: https://github.com/cschneid-the-elder/mapa
- **License**: MIT License
- **Target Commit**: master branch
- **Grammar Files**: `JCLLexer.g4`, `JCLParser.g4`

## How to regenerate Python parser files:

Run `port_g4.py` script to update Python 3 lexer actions if `JCLLexer.g4` changes:
```bash
python backend/domain/structural_graph/_jcl/grammar/port_g4.py
```

Generate Python files with ANTLR 4 jar:
```bash
java -jar backend/tools/antlr/antlr-4.13.2-complete.jar -Dlanguage=Python3 -o backend/domain/structural_graph/_jcl/generated backend/domain/structural_graph/_jcl/grammar/JCLLexer.g4 backend/domain/structural_graph/_jcl/grammar/JCLParser.g4
```
