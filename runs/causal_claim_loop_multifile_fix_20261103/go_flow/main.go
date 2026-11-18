// go-flow emits lexical AST facts. It never runs or imports the target source.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/scanner"
	"go/token"
	"os"
	"sort"
	"strconv"
)

type Span struct {
	Start     int    `json:"start_line"`
	End       int    `json:"end_line"`
	Column    int    `json:"start_column"`
	EndColumn int    `json:"end_column"`
	Offset    int    `json:"start_offset"`
	EndOffset int    `json:"end_offset"`
	Code      string `json:"code"`
}
type Binding struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Line int    `json:"line"`
	Kind string `json:"kind"`
}
type Call struct {
	Span
	Name        string      `json:"name"`
	Callee      string      `json:"callee"`
	Line        int         `json:"line"`
	Column      int         `json:"column"`
	Args        []Span      `json:"args"`
	ArgBindings [][]Binding `json:"arg_bindings"`
	Assigned    []Binding   `json:"assigned"`
	LocalTarget string      `json:"local_target"`
	Resolution  string      `json:"resolution"`
}
type Fact struct {
	Span
	Relation   string    `json:"relation"`
	From       []Binding `json:"from"`
	To         []Binding `json:"to"`
	Slot       int       `json:"slot"`
	Unresolved []string  `json:"unresolved"`
}
type Unit struct {
	Span
	NameSpan   *Span     `json:"name_span,omitempty"`
	ID         string    `json:"id"`
	Kind       string    `json:"kind"`
	Name       string    `json:"name"`
	Params     []Binding `json:"params"`
	Receiver   []Binding `json:"receiver"`
	Variadic   bool      `json:"variadic"`
	Calls      []Call    `json:"calls"`
	Facts      []Fact    `json:"facts"`
	Conditions []Span    `json:"conditions"`
}
type Output struct {
	Path                string           `json:"path"`
	SHA                 string           `json:"sha256"`
	Units               []Unit           `json:"units"`
	Errors              []string         `json:"errors"`
	Limitations         []string         `json:"limitations"`
	StructuralSignature []map[string]any `json:"structural_signature"`
	SignatureLimits     []string         `json:"structural_signature_limits"`
	Comments            [][2]int         `json:"comments"`
	Identifiers         []Span           `json:"identifiers"`
}

func main() {
	var req struct {
		Path string `json:"path"`
	}
	if json.NewDecoder(os.Stdin).Decode(&req) != nil {
		os.Exit(2)
	}
	out := Output{Path: req.Path, Units: []Unit{}, Errors: []string{}, Limitations: []string{"Lexical binding only; not CFG/path-sensitive, SSA, heap alias analysis, or runtime dispatch proof", "All return branches are candidates; feasibility is unresolved"}}
	out.StructuralSignature = []map[string]any{}
	out.SignatureLimits = []string{"Syntax signature only, not behavioral equivalence", "No go/types/ABI/layout or build-tag reachability analysis", "Flow does not implement named/naked returns, tuple expansion, heap aliasing or path feasibility"}
	raw, err := os.ReadFile(req.Path)
	if err != nil {
		out.Errors = append(out.Errors, err.Error())
		json.NewEncoder(os.Stdout).Encode(out)
		return
	}
	h := sha256.Sum256(raw)
	out.SHA = hex.EncodeToString(h[:])
	fs := token.NewFileSet()
	f, err := parser.ParseFile(fs, req.Path, raw, parser.AllErrors|parser.ParseComments)
	if err != nil {
		out.Errors = append(out.Errors, err.Error())
		json.NewEncoder(os.Stdout).Encode(out)
		return
	}
	tf := fs.File(f.Pos())
	out.Comments = [][2]int{}
	for _, group := range f.Comments {
		groupStart, groupEnd := tf.Offset(group.Pos()), tf.Offset(group.End())
		for _, comment := range group.List {
			start := tf.Offset(comment.Pos())
			end := tf.Offset(comment.End())
			// go/ast strips CR from comment text, so End can undercount CRLF.
			if bytes.HasPrefix(raw[start:], []byte("//")) {
				if n := bytes.IndexByte(raw[start:], '\n'); n >= 0 {
					end = start + n
				} else {
					end = len(raw)
				}
			} else if bytes.HasPrefix(raw[start:], []byte("/*")) {
				if n := bytes.Index(raw[start+2:], []byte("*/")); n >= 0 {
					end = start + 2 + n + 2
				}
			}
			if end > groupEnd {
				groupEnd = end
			}
		}
		// The preserved source conditions masked a whole CommentGroup envelope,
		// including indentation between adjacent comments. Keep that exact row
		// representation rather than only masking each comment token.
		out.Comments = append(out.Comments, [2]int{groupStart, groupEnd})
	}
	rawEnds := map[int]int{}
	ast.Inspect(f, func(n ast.Node) bool {
		if b, ok := n.(*ast.BasicLit); ok && b.Kind == token.STRING {
			s := tf.Offset(b.Pos())
			if s < len(raw) && raw[s] == '`' {
				if e := bytes.IndexByte(raw[s+1:], '`'); e >= 0 {
					rawEnds[tf.Offset(b.End())] = s + e + 2
				}
			}
		}
		return true
	})
	span := func(n ast.Node) Span {
		if n == nil {
			return Span{}
		}
		s, e := tf.Offset(n.Pos()), tf.Offset(n.End())
		if x, ok := rawEnds[e]; ok {
			e = x
		}
		a, b := tf.PositionFor(n.Pos(), false), tf.PositionFor(tf.Pos(e), false)
		return Span{a.Line, b.Line, a.Column, b.Column, s, e, string(raw[s:e])}
	}
	out.Identifiers = []Span{}
	ast.Inspect(f, func(n ast.Node) bool {
		if ident, ok := n.(*ast.Ident); ok {
			out.Identifiers = append(out.Identifiers, span(ident))
		}
		return true
	})
	syntax := func(s Span) []string {
		var scan scanner.Scanner
		file := token.NewFileSet().AddFile("syntax", -1, len([]byte(s.Code)))
		scan.Init(file, []byte(s.Code), nil, 0)
		tokens := []string{}
		for {
			_, tok, lit := scan.Scan()
			if tok == token.EOF {
				break
			}
			if tok == token.SEMICOLON {
				continue
			}
			if lit != "" {
				tokens = append(tokens, tok.String()+":"+lit)
			} else {
				tokens = append(tokens, tok.String())
			}
		}
		return tokens
	}
	namesOnly := func(bs []Binding) []string {
		names := []string{}
		for _, b := range bs {
			names = append(names, b.Name)
		}
		return names
	}
	fieldSyntax := func(f *ast.FieldList) []string {
		if f == nil {
			return []string{}
		}
		return syntax(span(f))
	}
	bind := func(id *ast.Ident) Binding {
		if id == nil {
			return Binding{}
		}
		p := id.Pos()
		kind := "unresolved"
		key := "unresolved:" + id.Name
		if id.Obj != nil {
			p = id.Obj.Pos()
			kind = id.Obj.Kind.String()
			key = "B" + strconv.Itoa(tf.Offset(p)) + ":" + id.Name
		}
		return Binding{key, id.Name, tf.PositionFor(p, false).Line, kind}
	}
	bindings := func(n ast.Node) []Binding {
		result := []Binding{}
		seen := map[string]bool{}
		ast.Inspect(n, func(x ast.Node) bool {
			if _, ok := x.(*ast.FuncLit); ok {
				return false
			}
			if id, ok := x.(*ast.Ident); ok && id.Obj != nil {
				b := bind(id)
				if !seen[b.ID] {
					result = append(result, b)
					seen[b.ID] = true
				}
			}
			return true
		})
		return result
	}
	parents := map[ast.Node]ast.Node{}
	stack := []ast.Node{}
	writes := map[*ast.Object]int{}
	ast.Inspect(f, func(n ast.Node) bool {
		if n == nil {
			stack = stack[:len(stack)-1]
			return true
		}
		if len(stack) > 0 {
			parents[n] = stack[len(stack)-1]
		}
		stack = append(stack, n)
		switch x := n.(type) {
		case *ast.AssignStmt:
			for _, left := range x.Lhs {
				if id, ok := left.(*ast.Ident); ok && id.Obj != nil {
					writes[id.Obj]++
				}
			}
		case *ast.ValueSpec:
			for _, id := range x.Names {
				if id.Obj != nil {
					writes[id.Obj]++
				}
			}
		}
		return true
	})
	var bodies []ast.Node
	unitIDs := map[token.Pos]string{}
	functionDefs := map[token.Pos]string{}
	ast.Inspect(f, func(n ast.Node) bool {
		switch x := n.(type) {
		case *ast.FuncDecl:
			if x.Body != nil {
				bodies = append(bodies, x)
				unitIDs[x.Pos()] = "U" + strconv.Itoa(tf.Offset(x.Pos()))
				functionDefs[x.Name.Pos()] = unitIDs[x.Pos()]
			}
		case *ast.FuncLit:
			bodies = append(bodies, x)
			unitIDs[x.Pos()] = "U" + strconv.Itoa(tf.Offset(x.Pos()))
		}
		return true
	})
	var resolveLocal func(ast.Expr, map[*ast.Object]bool) (string, string)
	resolveLocal = func(e ast.Expr, seen map[*ast.Object]bool) (string, string) {
		switch x := e.(type) {
		case *ast.FuncLit:
			return unitIDs[x.Pos()], "direct_function_literal"
		case *ast.ParenExpr:
			return resolveLocal(x.X, seen)
		case *ast.Ident:
			if x.Obj == nil {
				return "", "identifier_binding_unresolved"
			}
			if id := functionDefs[x.Obj.Pos()]; id != "" {
				return id, "direct_lexical_function"
			}
			if writes[x.Obj] > 1 {
				return "", "alias_reassignment_unresolved"
			}
			if seen[x.Obj] {
				return "", "alias_cycle"
			}
			seen[x.Obj] = true
			switch d := x.Obj.Decl.(type) {
			case *ast.AssignStmt:
				if len(d.Lhs) == len(d.Rhs) {
					for i, l := range d.Lhs {
						if a, ok := l.(*ast.Ident); ok && a.Obj == x.Obj {
							target, why := resolveLocal(d.Rhs[i], seen)
							if target != "" {
								return target, "function_alias:" + why
							}
						}
					}
				}
			case *ast.ValueSpec:
				if len(d.Names) == len(d.Values) {
					for i, l := range d.Names {
						if l.Obj == x.Obj {
							return resolveLocal(d.Values[i], seen)
						}
					}
				}
			}
			return "", "callee_variable_or_reassignment_unresolved"
		}
		return "", "qualified_or_dynamic_dispatch_requires_native_binding"
	}
	for _, node := range bodies {
		var ft *ast.FuncType
		var receiverFields *ast.FieldList
		var body *ast.BlockStmt
		u := Unit{Span: span(node), ID: unitIDs[node.Pos()], Params: []Binding{}, Receiver: []Binding{}, Calls: []Call{}, Facts: []Fact{}, Conditions: []Span{}}
		switch x := node.(type) {
		case *ast.FuncDecl:
			receiverFields = x.Recv
			u.Kind = "function"
			u.Name = x.Name.Name
			nameSpan := span(x.Name)
			u.NameSpan = &nameSpan
			ft = x.Type
			body = x.Body
			if x.Recv != nil {
				for _, r := range x.Recv.List {
					for _, n := range r.Names {
						u.Receiver = append(u.Receiver, bind(n))
					}
				}
			}
		case *ast.FuncLit:
			u.Kind = "closure"
			u.Name = u.ID
			ft = x.Type
			body = x.Body
		}
		for _, p := range ft.Params.List {
			if _, ok := p.Type.(*ast.Ellipsis); ok {
				u.Variadic = true
			}
			for _, n := range p.Names {
				u.Params = append(u.Params, bind(n))
			}
		}
		captures := map[string]bool{}
		ast.Inspect(body, func(n ast.Node) bool {
			if n == nil {
				return true
			}
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			switch x := n.(type) {
			case *ast.AssignStmt:
				for i, r := range x.Rhs {
					to := []Binding{}
					if len(x.Lhs) == len(x.Rhs) {
						to = bindings(x.Lhs[i])
					} else {
						for _, l := range x.Lhs {
							to = append(to, bindings(l)...)
						}
					}
					rel := "expression_assignment_candidate"
					unknown := []string{"Expression dependency does not prove equal values or heap aliasing"}
					if _, ok := r.(*ast.Ident); ok {
						rel = "direct_assignment"
						unknown = []string{"Value copy proven syntactically; heap alias/type and later writes unresolved"}
					}
					if _, ok := r.(*ast.CallExpr); ok {
						rel = "call_result_assignment"
						unknown = []string{"Return slot/branch requires resolved callee", "No runtime dispatch proof"}
					}
					u.Facts = append(u.Facts, Fact{Span: span(x), Relation: rel, From: bindings(r), To: to, Slot: i, Unresolved: unknown})
				}
			case *ast.CallExpr:
				c := Call{Span: span(x), Callee: span(x.Fun).Code, Args: []Span{}, ArgBindings: [][]Binding{}, Assigned: []Binding{}}
				pos := x.Fun.Pos()
				switch z := x.Fun.(type) {
				case *ast.Ident:
					c.Name = z.Name
				case *ast.SelectorExpr:
					c.Name = z.Sel.Name
					pos = z.Sel.Pos()
				default:
					c.Name = "<expression>"
				}
				p := tf.PositionFor(pos, false)
				c.Line = p.Line
				c.Column = p.Column
				for _, a := range x.Args {
					c.Args = append(c.Args, span(a))
					c.ArgBindings = append(c.ArgBindings, bindings(a))
				}
				c.LocalTarget, c.Resolution = resolveLocal(x.Fun, map[*ast.Object]bool{})
				if a, ok := parents[x].(*ast.AssignStmt); ok {
					for _, r := range a.Rhs {
						if r == x {
							for _, l := range a.Lhs {
								c.Assigned = append(c.Assigned, bindings(l)...)
							}
						}
					}
				}
				u.Calls = append(u.Calls, c)
			case *ast.ReturnStmt:
				for i, r := range x.Results {
					u.Facts = append(u.Facts, Fact{Span: span(x), Relation: "return_value", From: bindings(r), To: []Binding{}, Slot: i, Unresolved: []string{"Return branch feasibility unresolved"}})
				}
			case *ast.IfStmt:
				u.Conditions = append(u.Conditions, span(x.Cond))
			case *ast.ForStmt:
				if x.Cond != nil {
					u.Conditions = append(u.Conditions, span(x.Cond))
				}
			case *ast.RangeStmt:
				u.Conditions = append(u.Conditions, span(x.X))
			case *ast.SwitchStmt:
				if x.Tag != nil {
					u.Conditions = append(u.Conditions, span(x.Tag))
				}
			case *ast.Ident:
				if u.Kind == "closure" && x.Obj != nil && x.Obj.Kind == ast.Var {
					p := tf.Offset(x.Obj.Pos())
					if p < u.Offset || p >= u.EndOffset {
						b := bind(x)
						if !captures[b.ID] {
							captures[b.ID] = true
							u.Facts = append(u.Facts, Fact{Span: span(x), Relation: "closure_capture", From: []Binding{b}, To: []Binding{}, Unresolved: []string{"Lexical capture only; invocation timing, mutation and Go-version-specific loop capture semantics unresolved"}})
						}
					}
				}
			}
			return true
		})
		sort.Slice(u.Calls, func(i, j int) bool { return u.Calls[i].Offset < u.Calls[j].Offset })
		sort.Slice(u.Facts, func(i, j int) bool {
			if u.Facts[i].Offset != u.Facts[j].Offset {
				return u.Facts[i].Offset < u.Facts[j].Offset
			}
			return u.Facts[i].Relation < u.Facts[j].Relation
		})
		out.Units = append(out.Units, u)
		callsSignature := []map[string]any{}
		factsSignature := []map[string]any{}
		conditionSignature := [][]string{}
		for _, c := range u.Calls {
			args := [][]string{}
			for _, a := range c.Args {
				args = append(args, syntax(a))
			}
			callsSignature = append(callsSignature, map[string]any{"name": c.Name, "args": args, "assigned": namesOnly(c.Assigned)})
		}
		for _, f := range u.Facts {
			factsSignature = append(factsSignature, map[string]any{"relation": f.Relation, "from": namesOnly(f.From), "to": namesOnly(f.To), "slot": f.Slot, "syntax": syntax(f.Span)})
		}
		for _, c := range u.Conditions {
			conditionSignature = append(conditionSignature, syntax(c))
		}
		name := u.Name
		if u.Kind == "closure" {
			name = "<closure>"
		}
		out.StructuralSignature = append(out.StructuralSignature, map[string]any{"name": name, "kind": u.Kind, "params": namesOnly(u.Params), "receiver": namesOnly(u.Receiver), "parameter_signature": fieldSyntax(ft.Params), "return_signature": fieldSyntax(ft.Results), "type_parameters": fieldSyntax(ft.TypeParams), "receiver_signature": fieldSyntax(receiverFields), "variadic": u.Variadic, "calls": callsSignature, "facts": factsSignature, "conditions": conditionSignature})
	}
	for _, decl := range f.Decls {
		if d, ok := decl.(*ast.GenDecl); ok {
			for _, spec := range d.Specs {
				if t, ok := spec.(*ast.TypeSpec); ok {
					out.StructuralSignature = append(out.StructuralSignature, map[string]any{"kind": "type_metadata", "name": t.Name.Name, "syntax": syntax(span(t))})
				}
			}
		}
	}
	sort.Slice(out.Units, func(i, j int) bool { return out.Units[i].Offset < out.Units[j].Offset })
	json.NewEncoder(os.Stdout).Encode(out)
}
