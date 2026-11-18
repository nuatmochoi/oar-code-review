// ace-go-dataflow emits conservative, syntax-level intra-procedural def-use evidence.
// It is a smoke-test provider; confirmatory runs should replace it with CodeQL/SSA.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type request struct {
	ID       string `json:"id"`
	Diff     string `json:"diff"`
	RepoPath string `json:"repo_path"`
}

type block struct {
	ID         string         `json:"id"`
	Kind       string         `json:"kind"`
	Content    string         `json:"content"`
	File       string         `json:"file"`
	StartLine  int            `json:"start_line"`
	EndLine    int            `json:"end_line"`
	Query      string         `json:"query"`
	Relation   string         `json:"relation"`
	Confidence float64        `json:"confidence"`
	TokenCost  int            `json:"token_cost"`
	Metadata   map[string]any `json:"metadata"`
}

var hunkPattern = regexp.MustCompile(`\+(\d+)`)
var identifierPattern = regexp.MustCompile(`[A-Za-z_][A-Za-z0-9_]{2,}`)

func changed(diff string) map[string]map[int]bool {
	result := map[string]map[int]bool{}
	file := ""
	lineNumber := 0
	for _, line := range strings.Split(diff, "\n") {
		switch {
		case strings.HasPrefix(line, "+++ b/"):
			file = strings.TrimPrefix(line, "+++ b/")
			if result[file] == nil {
				result[file] = map[int]bool{}
			}
		case strings.HasPrefix(line, "@@"):
			if match := hunkPattern.FindStringSubmatch(line); match != nil {
				lineNumber, _ = strconv.Atoi(match[1])
			}
		case strings.HasPrefix(line, "-") && !strings.HasPrefix(line, "---"):
			continue
		case strings.HasPrefix(line, "+") && !strings.HasPrefix(line, "+++"):
			if file != "" {
				result[file][lineNumber] = true
			}
			lineNumber++
		default:
			if file != "" && lineNumber > 0 {
				lineNumber++
			}
		}
	}
	return result
}

func nodeIdentifiers(node ast.Node) map[string]bool {
	result := map[string]bool{}
	ast.Inspect(node, func(n ast.Node) bool {
		if id, ok := n.(*ast.Ident); ok && identifierPattern.MatchString(id.Name) {
			result[id.Name] = true
		}
		return true
	})
	return result
}

func relevantNode(n ast.Node) bool {
	switch n.(type) {
	case *ast.AssignStmt, *ast.DeclStmt, *ast.ReturnStmt, *ast.IfStmt, *ast.ForStmt, *ast.RangeStmt, *ast.ExprStmt, *ast.SendStmt:
		return true
	}
	return false
}

func main() {
	var req request
	if err := json.NewDecoder(os.Stdin).Decode(&req); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	changes := changed(req.Diff)
	blocks := []block{}
	for relative, linesChanged := range changes {
		if filepath.Ext(relative) != ".go" {
			continue
		}
		path := filepath.Join(req.RepoPath, filepath.FromSlash(relative))
		content, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		fset := token.NewFileSet()
		parsed, err := parser.ParseFile(fset, path, content, 0)
		if err != nil {
			continue
		}
		sourceLines := strings.Split(string(content), "\n")
		for _, decl := range parsed.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				continue
			}
			start, end := fset.Position(fn.Pos()).Line, fset.Position(fn.End()).Line
			containsChange := false
			for line := range linesChanged {
				if line >= start && line <= end {
					containsChange = true
					break
				}
			}
			if !containsChange {
				continue
			}
			seed := map[string]bool{}
			for line := range linesChanged {
				if line >= 1 && line <= len(sourceLines) {
					for _, id := range identifierPattern.FindAllString(sourceLines[line-1], -1) {
						seed[id] = true
					}
				}
			}
			evidence := []string{}
			seen := map[int]bool{}
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				if n == nil || !relevantNode(n) {
					return true
				}
				ids := nodeIdentifiers(n)
				intersects := false
				for id := range seed {
					if ids[id] {
						intersects = true
						break
					}
				}
				if !intersects {
					return true
				}
				startPosition := fset.Position(n.Pos())
				endPosition := fset.Position(n.End())
				line := startPosition.Line
				if seen[line] || line < 1 || line > len(sourceLines) {
					return true
				}
				seen[line] = true
				snippet := strings.TrimSpace(string(content[startPosition.Offset:endPosition.Offset]))
				snippet = strings.Join(strings.Fields(snippet), " ")
				if len(snippet) > 500 {
					snippet = snippet[:500] + "..."
				}
				evidence = append(evidence, fmt.Sprintf("%s:%d  %s", relative, line, snippet))
				return true
			})
			if len(evidence) == 0 {
				continue
			}
			sort.Strings(evidence)
			seeds := make([]string, 0, len(seed))
			for id := range seed {
				seeds = append(seeds, id)
			}
			sort.Strings(seeds)
			text := fmt.Sprintf("Function: %s\nChanged identifiers: %s\nPotential intra-procedural def-use statements:\n%s", fn.Name.Name, strings.Join(seeds, ", "), strings.Join(evidence, "\n"))
			blocks = append(blocks, block{
				ID: fmt.Sprintf("DF-%02d", len(blocks)+1), Kind: "dataflow", Content: text,
				File: relative, StartLine: start, EndLine: end, Query: strings.Join(seeds, ","),
				Relation: "intra_procedural_def_use", Confidence: 0.6,
				Metadata: map[string]any{"provider": "go-ast", "prototype_only": true, "path_sensitive": false},
			})
		}
	}
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()
	if err := json.NewEncoder(writer).Encode(blocks); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}
