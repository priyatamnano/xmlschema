#
# Copyright (c), 2016-2020, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
"""
This module defines a proxy class and a mixin class for enabling XPath on schemas.
"""
import sys
from abc import abstractmethod
from typing import cast, overload, Any, Dict, Iterator, List, Optional, \
    Sequence, Set, TypeVar, Union
import re

from elementpath import TypedElement, XPath2Parser, \
    XPathSchemaContext, AbstractSchemaProxy, protocols

from .exceptions import XMLSchemaValueError, XMLSchemaTypeError
from .names import XSD_NAMESPACE
from .aliases import NamespacesType, SchemaType, BaseXsdType, XPathElementType
from .helpers import get_qname, local_name, get_prefixed_qname

if sys.version_info < (3, 8):
    XMLSchemaProtocol = SchemaType
    ElementProtocol = XPathElementType
    XsdTypeProtocol = BaseXsdType
else:
    from typing import runtime_checkable, Protocol

    XsdTypeProtocol = protocols.XsdTypeProtocol

    class XMLSchemaProtocol(protocols.XMLSchemaProtocol, Protocol):
        attributes: Dict[str, Any]

    @runtime_checkable
    class ElementProtocol(protocols.ElementProtocol, Protocol):
        schema: XMLSchemaProtocol
        attributes: Dict[str, Any]


_REGEX_TAG_POSITION = re.compile(r'\b\[\d+]')


def iter_schema_nodes(root: Union[XMLSchemaProtocol, ElementProtocol], with_root: bool = True) \
        -> Iterator[Union[XMLSchemaProtocol, ElementProtocol]]:
    """
    Iteration function for schema nodes. It doesn't yield text nodes,
    that are always `None` for schema elements, and detects visited
    element in order to skip already visited nodes.

    :param root: schema or schema element.
    :param with_root: if `True` yields initial element.
    """
    if isinstance(root, TypedElement):
        root = cast(ElementProtocol, root.elem)

    nodes = {root}
    if with_root:
        yield root

    iterators: List[Any] = []
    children: Iterator[Any] = iter(root)

    while True:
        try:
            child = next(children)
        except StopIteration:
            try:
                children = iterators.pop()
            except IndexError:
                return
        else:
            if child in nodes:
                continue
            elif child.ref is not None:
                nodes.add(child)
                yield child
                if child.ref not in nodes:
                    nodes.add(child.ref)
                    yield child.ref
                    iterators.append(children)
                    children = iter(child.ref)
            else:
                nodes.add(child)
                yield child
                iterators.append(children)
                children = iter(child)


class XMLSchemaContext(XPathSchemaContext):
    """XPath dynamic schema context for the *xmlschema* library."""
    _iter_nodes = staticmethod(iter_schema_nodes)


class XMLSchemaProxy(AbstractSchemaProxy):
    """XPath schema proxy for the *xmlschema* library."""
    _schema: SchemaType  # type: ignore[assignment]

    def __init__(self, schema: Optional[XMLSchemaProtocol] = None,
                 base_element: Optional[ElementProtocol] = None) -> None:

        if schema is None:
            from xmlschema import XMLSchema10
            schema = cast(XMLSchemaProtocol, getattr(XMLSchema10, 'meta_schema', None))

        super(XMLSchemaProxy, self).__init__(schema, base_element)

        if base_element is not None:
            try:
                if base_element.schema is not schema:
                    msg = "{} is not an element of {}"
                    raise XMLSchemaValueError(msg.format(base_element, schema))
            except AttributeError:
                raise XMLSchemaTypeError("%r is not an XsdElement" % base_element)

    def bind_parser(self, parser: XPath2Parser) -> None:
        parser.schema = self
        parser.symbol_table = dict(parser.__class__.symbol_table)

        with self._schema.lock:
            if self._schema.xpath_tokens is None:
                self._schema.xpath_tokens = {
                    xsd_type.name: parser.schema_constructor(xsd_type.name)
                    for xsd_type in self.iter_atomic_types() if xsd_type.name
                }

        parser.symbol_table.update(self._schema.xpath_tokens)

    def get_context(self) -> XMLSchemaContext:
        return XMLSchemaContext(
            root=self._schema,  # type: ignore[arg-type]
            namespaces=dict(self._schema.namespaces),
            item=self._base_element
        )

    def is_instance(self, obj: Any, type_qname: str) -> bool:
        # FIXME: use elementpath.datatypes for checking atomic datatypes
        xsd_type = self._schema.maps.types[type_qname]
        if isinstance(xsd_type, tuple):
            from .validators import XMLSchemaNotBuiltError
            msg = "XSD type %r is not built"
            raise XMLSchemaNotBuiltError(xsd_type[1], msg % type_qname)

        try:
            xsd_type.encode(obj)
        except ValueError:
            return False
        else:
            return True

    def cast_as(self, obj: Any, type_qname: str) -> Any:
        xsd_type = self._schema.maps.types[type_qname]
        if isinstance(xsd_type, tuple):
            from .validators import XMLSchemaNotBuiltError
            msg = "XSD type %r is not built"
            raise XMLSchemaNotBuiltError(xsd_type[1], msg % type_qname)
        return xsd_type.decode(obj)

    def iter_atomic_types(self) -> Iterator[XsdTypeProtocol]:
        for xsd_type in self._schema.maps.types.values():
            if not isinstance(xsd_type, tuple) and \
                    xsd_type.target_namespace != XSD_NAMESPACE and \
                    hasattr(xsd_type, 'primitive_type'):
                yield cast(XsdTypeProtocol, xsd_type)

    def get_primitive_type(self, xsd_type: XsdTypeProtocol) -> XsdTypeProtocol:
        primitive_type = cast(BaseXsdType, xsd_type).root_type
        return cast(XsdTypeProtocol, primitive_type)


E = TypeVar('E', bound='ElementPathMixin[Any]')


class ElementPathMixin(Sequence[E]):
    """
    Mixin abstract class for enabling ElementTree and XPath 2.0 API on XSD components.

    :cvar text: the Element text, for compatibility with the ElementTree API.
    :cvar tail: the Element tail, for compatibility with the ElementTree API.
    """
    text: Optional[str] = None
    tail: Optional[str] = None
    name: Optional[str] = None
    attributes: Any = {}
    namespaces: Any = {}
    xpath_default_namespace = ''

    @abstractmethod
    def __iter__(self) -> Iterator[E]:
        raise NotImplementedError

    @overload
    def __getitem__(self, i: int) -> E: ...

    @overload
    def __getitem__(self, s: slice) -> Sequence[E]: ...

    def __getitem__(self, i: Union[int, slice]) -> Union[E, Sequence[E]]:
        try:
            return [e for e in self][i]
        except IndexError:
            raise IndexError('child index out of range')

    def __reversed__(self) -> Iterator[E]:
        return reversed([e for e in self])

    def __len__(self) -> int:
        return len([e for e in self])

    @property
    def tag(self) -> str:
        """Alias of the *name* attribute. For compatibility with the ElementTree API."""
        return self.name or ''

    @property
    def attrib(self) -> Any:
        """Returns the Element attributes. For compatibility with the ElementTree API."""
        return self.attributes

    def get(self, key: str, default: Any = None) -> Any:
        """Gets an Element attribute. For compatibility with the ElementTree API."""
        return self.attributes.get(key, default)

    @property
    def xpath_proxy(self) -> XMLSchemaProxy:
        """Returns an XPath proxy instance bound with the schema."""
        raise NotImplementedError

    def _get_xpath_namespaces(self, namespaces: Optional[NamespacesType] = None) \
            -> Dict[str, str]:
        """
        Returns a dictionary with namespaces for XPath selection.

        :param namespaces: an optional map from namespace prefix to namespace URI. \
        If this argument is not provided the schema's namespaces are used.
        """
        if namespaces is None:
            namespaces = {k: v for k, v in self.namespaces.items() if k}
            namespaces[''] = self.xpath_default_namespace
        elif '' not in namespaces:
            namespaces[''] = self.xpath_default_namespace

        xpath_namespaces: Dict[str, str] = XPath2Parser.DEFAULT_NAMESPACES.copy()
        xpath_namespaces.update(namespaces)
        return xpath_namespaces

    def is_matching(self, name: Optional[str], default_namespace: Optional[str] = None) -> bool:
        if not name or name[0] == '{' or not default_namespace:
            return self.name == name
        else:
            return self.name == '{%s}%s' % (default_namespace, name)

    def find(self, path: str, namespaces: Optional[NamespacesType] = None) -> Optional[E]:
        """
        Finds the first XSD subelement matching the path.

        :param path: an XPath expression that considers the XSD component as the root element.
        :param namespaces: an optional mapping from namespace prefix to namespace URI.
        :return: the first matching XSD subelement or ``None`` if there is no match.
        """
        path = _REGEX_TAG_POSITION.sub('', path.strip())  # Strips tags positions from path
        namespaces = self._get_xpath_namespaces(namespaces)
        parser = XPath2Parser(namespaces, strict=False)
        context = XMLSchemaContext(self)  # type: ignore[arg-type]

        return cast(Optional[E], next(parser.parse(path).select_results(context), None))

    def findall(self, path: str, namespaces: Optional[NamespacesType] = None) -> List[E]:
        """
        Finds all XSD subelements matching the path.

        :param path: an XPath expression that considers the XSD component as the root element.
        :param namespaces: an optional mapping from namespace prefix to full name.
        :return: a list containing all matching XSD subelements in document order, an empty \
        list is returned if there is no match.
        """
        path = _REGEX_TAG_POSITION.sub('', path.strip())  # Strips tags positions from path
        namespaces = self._get_xpath_namespaces(namespaces)
        parser = XPath2Parser(namespaces, strict=False)
        context = XMLSchemaContext(self)  # type: ignore[arg-type]

        return cast(List[E], parser.parse(path).get_results(context))

    def iterfind(self, path: str, namespaces: Optional[NamespacesType] = None) -> Iterator[E]:
        """
        Creates and iterator for all XSD subelements matching the path.

        :param path: an XPath expression that considers the XSD component as the root element.
        :param namespaces: is an optional mapping from namespace prefix to full name.
        :return: an iterable yielding all matching XSD subelements in document order.
        """
        path = _REGEX_TAG_POSITION.sub('', path.strip())  # Strips tags positions from path
        namespaces = self._get_xpath_namespaces(namespaces)
        parser = XPath2Parser(namespaces, strict=False)
        context = XMLSchemaContext(self)  # type: ignore[arg-type]

        return cast(Iterator[E], parser.parse(path).select_results(context))

    def iter(self, tag: Optional[str] = None) -> Iterator[E]:
        """
        Creates an iterator for the XSD element and its subelements. If tag is not `None` or '*',
        only XSD elements whose matches tag are returned from the iterator. Local elements are
        expanded without repetitions. Element references are not expanded because the global
        elements are not descendants of other elements.
        """
        def safe_iter(elem: Any) -> Iterator[E]:
            if tag is None or elem.is_matching(tag):
                yield elem
            for child in elem:
                if child.parent is None:
                    yield from safe_iter(child)
                elif getattr(child, 'ref', None) is not None:
                    if tag is None or child.is_matching(tag):
                        yield child
                elif child not in local_elements:
                    local_elements.add(child)
                    yield from safe_iter(child)

        if tag == '*':
            tag = None
        local_elements: Set[E] = set()
        return safe_iter(self)

    def iterchildren(self, tag: Optional[str] = None) -> Iterator[E]:
        """
        Creates an iterator for the child elements of the XSD component. If *tag* is not `None`
        or '*', only XSD elements whose name matches tag are returned from the iterator.
        """
        if tag == '*':
            tag = None
        for child in self:
            if tag is None or child.is_matching(tag):
                yield child


class XPathElement(ElementPathMixin['XPathElement']):
    """An element node for making XPath operations on schema types."""
    name: str
    parent = None

    def __init__(self, name: str, xsd_type: BaseXsdType) -> None:
        self.name = name
        self.type = xsd_type
        self.attributes = getattr(xsd_type, 'attributes', {})

    def __iter__(self) -> Iterator['XPathElement']:
        if not self.type.has_simple_content():
            yield from self.type.content.iter_elements()  # type: ignore[union-attr]

    @property
    def xpath_proxy(self) -> XMLSchemaProxy:
        return XMLSchemaProxy(
            cast(XMLSchemaProtocol, self.schema),
            cast(ElementProtocol, self)
        )

    @property
    def schema(self) -> SchemaType:
        return self.type.schema

    @property
    def target_namespace(self) -> str:
        return self.type.schema.target_namespace

    @property
    def namespaces(self) -> NamespacesType:
        return self.type.schema.namespaces

    @property
    def local_name(self) -> str:
        return local_name(self.name)

    @property
    def qualified_name(self) -> str:
        return get_qname(self.target_namespace, self.name)

    @property
    def prefixed_name(self) -> str:
        return get_prefixed_qname(self.name, self.namespaces)
