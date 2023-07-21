import collections
import pathlib
import sys
import textwrap
import types
import pprint
from functools import cached_property

from modelx.core import mxsys
from modelx.core.base import Interface
from modelx.core.parent import BaseParent
from modelx.core.model import Model
from modelx.core.space import BaseSpace
from modelx.core.cells import Cells
from modelx.serialize.ziputil import write_str_utf8, copy_file
from modelx.core.util import abs_to_rel_tuple

from .modulass import FormulaTransformer, lambda_to_func

this_dir = pathlib.Path(__file__).parent

MODEL_MODULE = '_mx_model'
SPACE_MODULE = '_mx_classes'
DATA_MODULE = '_mx_io'
SPACE_PKG_PREFIX = '_m_'
SPACE_CLS_PREFIX = '_c_'


class Exporter:

    def __init__(self, model: Model, path):
        self.model = model
        self.path = pathlib.Path(path)

    def gen_parents(self):
        """Generator yielding model and spaces in breadth-first order"""
        que = collections.deque([self.model])
        while que:
            parent = que.popleft()
            yield parent
            for child in parent.spaces.values():
                que.append(child)

    def export(self):
        io_manager = DataManager(self.model)

        # Create self.path dir and Write Model module
        write_str_utf8(
            ModelTranslator(self.model, io_manager).code,
            self.path / (MODEL_MODULE + '.py'))

        # Write _mx_sys.py
        copy_file(this_dir / '_mx_sys.py', self.path / '_mx_sys.py')

        for parent in self.gen_parents():
            if not parent.spaces:
                continue

            cur_dir = self.path / "/".join(
                SPACE_PKG_PREFIX + n for n in parent.fullname.split(".")[1:])

            # Write space modules
            write_str_utf8(
                SpaceTranslator(parent, io_manager).code,
                cur_dir / (SPACE_MODULE + '.py'))

            if parent is not self.model:
                init_line = f"from . import {SPACE_MODULE}"
            else:
                init_line = ''
            write_str_utf8(init_line, cur_dir / '__init__.py')

        # Write IO metadata
        ios = io_manager.get_literal_ios()
        specs = io_manager.get_literal_specs()

        if io_manager.ios or io_manager.iospecs:
            code = textwrap.dedent("""\
            ios = (
            {ios})

            iospecs = (
            {iospecs})
            """).format(ios=ios, iospecs=specs)
            write_str_utf8(
                code,
                self.path / (DATA_MODULE + '.py'))
            io_manager.write_ios(self.path)


class DataManager:

    def __init__(self, model: Model):
        self.model = model
        self.iospecs = {id(spec.value): spec for spec in model.iospecs}
        self.ios = {id(v): v for v in mxsys.iomanager.get_ios(model).values()}
        self.pickle_data = {}   # id(value): value

    def get_code(self, value):
        key = id(value)
        if key in self.iospecs:
            return f"io_data[{key}]"
        else:   # pickle
            if key not in self.pickle_data:
                self.pickle_data[key] = value
            else:
                assert value is self.pickle_data[key]
            return f"pickle_data[{key}]"

    def get_literal_ios(self):
        ios = {
            pathlib.PurePath(v.path).as_posix(): {
                'type': v.__class__.__name__,
                **v.persistent_args
            }
            for v in self.ios.values()}
        return pprint.pformat(ios, sort_dicts=False)

    def get_literal_specs(self):
        specs = {k: {'type': v.__class__.__name__,
                     'io': pathlib.PurePath(v.io.path).as_posix(),
                     'kwargs': v._on_serialize({})}
                 for k, v in self.iospecs.items()}
        return pprint.pformat(specs, sort_dicts=False)

    def write_ios(self, root):
        mxsys.iomanager.write_ios(root)


class ParentTranslator:

    module_template = ''
    space_dict_template = textwrap.dedent("""\
    self._mx_spaces = {{
    {elements}
    }}
    """)

    def __init__(self, parent: BaseParent, io_manager: DataManager):
        self.parent = parent
        self.io_manager = io_manager

    @cached_property
    def code(self):
        return self.module_template.format(
            dots=self.dots,
            SPACE_MODULE=SPACE_MODULE,
            child_imports=self.child_imports,
            name=self.parent.name,
            class_defs=self.class_defs,
        )

    @cached_property
    def dots(self):
        return '.' * len(self.parent._idtuple)

    @cached_property
    def child_imports(self):
        result = []
        for k, v in self.parent.spaces.items():
            if v.spaces:
                result.append('from . import ' + SPACE_PKG_PREFIX + k)

        return '\n'.join(result)

    def class_defs(self):
        raise NotImplementedError

    def ref_assigns(self, parent):
        result = []
        for k, v in parent.refs.items():
            if k[0] != "_":
                result.append('self.' + k + ' = ' + self.ref_value(parent, v))

        if result:
            result.insert(0, "# Reference assignment")
        else:
            result.append('pass')

        return "\n".join(result)

    def ref_value(self, parent, value):

        literal_types = [bool, int, float, str, type(None)]
        if isinstance(value, Interface):
            if value._is_valid():
                ids = list(abs_to_rel_tuple(value._idtuple, parent._idtuple))
                # example of ids -> attrs:
                # ('...', 'foo', 'bar') -> ['self', '_parent', '_parent', 'foo', 'bar']
                attrs = ['self'] + ['_parent'] * (len(ids[0]) - 1) + ids[1:]
                return '.'.join(attrs)
            else:
                return 'None'
        elif any(type(value) is t for t in literal_types):
            return str(value)
        elif (isinstance(value, types.ModuleType)
              and value in sys.modules.values()):
            # Module
            return "_mx_sys.import_module('" + value.__name__ + "')"
        else:   # Save Data or Pickle
            return self.io_manager.get_code(value)

    def space_dict(self, parent):
        elms = []
        for k, v in parent.spaces.items():
            if k[0] != "_":
                elms.append("'" + k + "'" + ': self.' + k)

        return self.space_dict_template.format(
            elements=textwrap.indent(",\n".join(elms), ' ' * 4)
        )


class ModelTranslator(ParentTranslator):
    
    module_template = textwrap.dedent("""\
    from . import _mx_sys
    from . import {SPACE_MODULE}
    
    {class_defs}
    
    mx_model = {name} = _c_{name}()
    """)
    
    class_template = textwrap.dedent("""\
    class _c_{name}(_mx_sys._mx_BaseModel):
    
        def __init__(self):
        
            # modelx variables
            self._parent = None
            self._model = self
                
    {space_assigns}
    {space_dict}
    
            self._mx_load_io()
    
        def _mx_assign_refs(self, io_data, pickle_data):

    {ref_assigns}
    """)

    @cached_property
    def class_defs(self):
        return self.class_template.format(
            name=self.parent.name,
            space_assigns=textwrap.indent(self.space_assigns(self.parent), ' ' * 8),
            space_dict=textwrap.indent(self.space_dict(self.parent), ' ' * 8),
            ref_assigns=textwrap.indent(self.ref_assigns(self.parent), ' ' * 8)
        )

    def space_assigns(self, parent):
        result = []
        for k, v in parent.spaces.items():
            if k[0] != "_":
                result.append(
                    'self.' + k + " = " + SPACE_MODULE + "." + SPACE_CLS_PREFIX + k + "(self)")
        if result:
            result.insert(0, "# Space assignments")

        return "\n".join(result)


class SpaceTranslator(ParentTranslator):

    module_template = textwrap.dedent("""\
    from {dots} import _mx_sys
    {child_imports}

    {class_defs}
    """)

    class_template = textwrap.dedent("""\
    class _c_{name}(_mx_sys._mx_BaseSpace):

        def __init__(self, parent):

            # modelx variables
            self._parent = parent
            self._model = parent._model

    {space_assigns}
    {space_dict}

    {cache_vars}

        def _mx_assign_refs(self, io_data, pickle_data):

    {ref_assigns}

    {methods}

    {cache_methods}

    """)

    cache_method_noparam = textwrap.dedent("""\
        def {name}(self):
            if self._has_{name}:
                return self._v_{name}
            else:
                val = self._v_{name} = self._f_{name}()
                self._has_{name} = True
                return val

    """)

    cache_method = textwrap.dedent("""\
        def {name}(self, {params}):
            if {idx_args} in self._v_{name}:
                return self._v_{name}[{idx_args}]
            else:
                val = self._f_{name}({args})
                self._v_{name}[{idx_args}] = val
                return val

    """)

    @cached_property
    def class_defs(self):
        defs = []
        for space in self.parent.spaces.values():
            defs.append(self._get_class_def(space))

        return '\n'.join(defs)

    def _get_class_def(self, space: BaseSpace):

        # Generate source.
        # To make sure to prefix refs with 'self.' that have builtin names,
        # Add dummy ref assignments to function definitions.
        # These assignments are removed by FormulaTransformer.
        lines = []
        for k, v in space.refs.items():
            if k[0] != '_':
                lines.append(k + ' = None')

        for k, v in space.cells.items():
            src = v.formula.source
            if src.strip()[:6] == "lambda":
                src = lambda_to_func(src, k)
            lines.append(src)

        source = '\n'.join(lines)

        cells = set()   # Pass cells names for replacing subscription
        for d in [space.cells, space.refs]:
            for k, v in d.items():
                if isinstance(v, Cells):
                    cells.add(k)

        trans = FormulaTransformer(source, cells)

        cache_vars = []
        cache_methods = []
        for func in trans.func_attrs.values():
            if func.param_len > 0:
                idx_args = func.args if func.param_len == 1 else func.tuplized_args
                cache_vars.append(
                    "self._v_" + func.name + " = {}")
                cache_methods.append(self.cache_method.format(
                    name=func.name,
                    params=func.params,
                    args=func.args,
                    idx_args=idx_args
                ))
            else:
                cache_vars.append(
                    "self._v_" + func.name + " = None")
                cache_vars.append(
                    "self._has_" + func.name + " = False")
                cache_methods.append(
                    self.cache_method_noparam.format(name=func.name))
        if cache_vars:
            cache_vars.insert(0, "# Cache variables")

        return self.class_template.format(
            name=space.name,
            space_assigns=textwrap.indent(self.space_assigns(space), ' ' * 8),
            space_dict=textwrap.indent(self.space_dict(space), ' ' * 8),
            cache_vars=textwrap.indent("\n".join(cache_vars), ' ' * 8),
            ref_assigns=textwrap.indent(self.ref_assigns(space), ' ' * 8),
            methods=textwrap.indent(trans.transformed.code, ' ' * 4),
            cache_methods=textwrap.indent(''.join(cache_methods), ' ' * 4))

    def space_assigns(self, parent):
        result = []
        for k, v in parent.spaces.items():
            if k[0] != "_":
                pkg = SPACE_PKG_PREFIX + parent.name + '.'
                result.append(
                    'self.' + k + " = " + pkg + SPACE_MODULE + "." + SPACE_CLS_PREFIX + k + "(self)")
        if result:
            result.insert(0, "# Space assignments")

        return "\n".join(result)