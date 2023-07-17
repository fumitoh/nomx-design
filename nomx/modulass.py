import builtins
from collections import namedtuple
import textwrap
import symtable
from typing import Optional


# from symtable import symtable, SymbolTable
import libcst as cst
from libcst.metadata import (
    GlobalScope, ClassScope, FunctionScope, ComprehensionScope, ParentNodeProvider)
import libcst.matchers as m


def list_symtable(source) -> list:
    table = symtable.symtable(source, "<string>", compile_type="exec")
    return _list_symtable_inner(table, [])


def _list_symtable_inner(table: symtable.SymbolTable, result: list):
    result.append(table)
    if table.has_children():
        for child in table.get_children():
            _list_symtable_inner(child, result)

    return result


def assert_scope_table_mapping(scope, table):

    assert len(scope) == len(table)

    # Global namespace
    s, t = scope[0], table[0]
    assert isinstance(s, cst.metadata.GlobalScope)
    assert t.get_type() == 'module'

    for s, t in zip(scope[1:], table[1:]):

        if isinstance(s, ClassScope):
            assert t.get_type() == 'class'
            assert s.name == t.get_name()

        elif isinstance(s, FunctionScope):
            assert t.get_type() == 'function'
            if s.name:
                assert s.name == t.get_name()
            else:
                assert t.get_name() == 'lambda'

        elif isinstance(s, ComprehensionScope):
            assert t.get_type() == 'function'
            # listcomp, dictcomp, setcomp, genexpr
            assert (name := t.get_name())[-4:] == 'comp' or name == 'genexpr'

        else:
            raise RuntimeError("must not happen")


FuncAttrs = namedtuple("FuncAttrs", ["name", "params", "param_len", "args"])


class FormulaTransformer(m.MatcherDecoratableTransformer):
    """Transform formulas to methods"""

    METADATA_DEPENDENCIES = (ParentNodeProvider,)
    matchers_compstats = m.If() | m.Try() | m.With() | m.For() | m.While()

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.prefix = "_f_"
        self.wrapper = cst.metadata.MetadataWrapper(cst.parse_module(source))
        self.module = self.wrapper.module
        self.node_to_scope = n_to_s = self.wrapper.resolve(cst.metadata.ScopeProvider)
        self.scopes = list(dict.fromkeys(n_to_s.values()))
        self.symtables = list_symtable(source)
        assert_scope_table_mapping(self.scopes, self.symtables)

        self.name_to_symbol = [
            {s.get_name(): s for s in table.get_symbols()} for table in self.symtables
        ]
        self.global_names = set()

        self.builtins = set(n for n in builtins.__dict__.keys()
                            if n[:2] != '__' or n[-2:] != '__')

        # state variables
        self.func_level = 0
        self.attr_stack = []
        self.topfunc_name = None

        self.func_attrs = {}
        self.transformed = self.wrapper.visit(self)

    def should_replace(self, node: cst.Name):

        # Name nodes in import statements are not in the keys of self.node_to_scope
        # For such names, their parents' scopes are looked for
        n = node
        while not (scope := self.node_to_scope.get(n, None)):
            prev = n
            n = self.get_metadata(ParentNodeProvider, n)
            if n == prev:
                raise RuntimeError(f"scope not found for {n.value}")

        i = next(i for i, v in enumerate(self.scopes) if scope == v)

        if symbol := self.name_to_symbol[i].get(node.value, None):
            if symbol.is_global():
                if symbol_top := self.name_to_symbol[0].get(node.value, None):
                    return symbol_top.is_global() and symbol_top.is_assigned()
                elif node.value in self.builtins:
                    return False
                else:
                    return True
            else:
                return False
        else:   # names between from and import, True, False, None
            return False

    @m.call_if_not_inside(m.FunctionDef())
    @m.leave(m.SimpleStatementLine() | matchers_compstats | m.Comment() | m.EmptyLine())
    def remove_statements(self, original_node, updated_node):
        """Remove all other than function defs at module level """
        if self.get_metadata(ParentNodeProvider, original_node) == self.module:
            return cst.RemoveFromParent()
        else:
            return updated_node

    def visit_FunctionDef(self, node: "FunctionDef") -> Optional[bool]:
        if self.func_level == 0:
            self.topfunc_name = node.name
        self.func_level += 1

    def leave_FunctionDef(
        self, original_node: "FunctionDef", updated_node: "FunctionDef"
    ):
        if self.func_level > 1:
            self.func_level -= 1
            return updated_node

        params = [p.name.value for p in original_node.params.params]
        args = ", ".join(params) if len(params) < 2 else "(" + ", ".join(params) + ")"

        self.func_attrs[original_node.name.value] = FuncAttrs(
            name=original_node.name.value,
            params=self.module.code_for_node(original_node.params),
            param_len=len(params),
            args=args
        )

        name = updated_node.name.with_changes(
            value=self.prefix + updated_node.name.value
        )

        self_param = cst.Param(name=cst.Name(value='self'))
        new_params = updated_node.params.with_changes(
            params=(self_param,) + tuple(updated_node.params.params)
        )

        self.topfunc_name = None
        self.func_level -= 1
        return updated_node.with_changes(
            name=name,
            params=new_params
        )

    def visit_Attribute(self, node: "Attribute") -> Optional[bool]:
        self.attr_stack.append(node.attr)

    def leave_Attribute(
        self, original_node: "Attribute", updated_node: "Attribute"
    ) -> "BaseExpression":
        self.attr_stack.pop()
        return updated_node

    def leave_Name(
        self, original_node: "Name", updated_node: "Name"
    ) -> "BaseExpression":

        if original_node == self.topfunc_name:
            return updated_node
        elif self.attr_stack and self.attr_stack[-1] == original_node:
            # Do nothing if node is an attribute of another name
            return updated_node
        elif self.should_replace(original_node):
            return cst.Attribute(value=cst.Name('self'), attr=updated_node)
        else:
            return updated_node


class_template = """\
class _c_{name}(_mxs._mx_BaseSpace):

    def __init__(self, parent):

        # modelx variables
        self._parent = parent

        # Cache variables
{cache_vars}

        # Recursive reference assignment
        self._mx_assign_refs()

    def _mx_assign_refs(self):

        # Reference assignment
        # ref_vars


{methods}


{cache_methods}

"""

cache_method_noparam = """\
    def {name}(self):
        if self._has_{name}:
            return self._v_{name}
        else:
            val = self._v_{name} = self._f_{name}()
            self._has_{name} = True
            return val

"""

cache_method = """\
    def {name}(self, {params}):
        if t in self._v_{name}:
            return self._v_{name}[{args}]
        else:
            val = self._f_{name}({args})
            self._v_{name}[{args}] = val
            return val

"""


def generate_source(name: str, source: str):

    trans = FormulaTransformer(source)
    cache_vars = []
    cache_methods = []
    for func in trans.func_attrs.values():
        if func.param_len > 0:
            cache_vars.append(
                "self._v_" + func.name + " = {}")
            cache_methods.append(cache_method.format(
                name=func.name,
                params=func.params,
                args=func.args))
        else:
            cache_vars.append(
                "self._v_" + func.name + " = None")
            cache_vars.append(
                "self._has_" + func.name + " = False")
            cache_methods.append(
                cache_method_noparam.format(name=func.name))

    return class_template.format(
        name=name,
        cache_vars=textwrap.indent("\n".join(cache_vars), ' '*8),
        methods=textwrap.indent(trans.transformed.code, ' '*4),
        cache_methods="".join(cache_methods)
    )
