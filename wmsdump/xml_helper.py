import io
import logging
from lxml import etree

logger = logging.getLogger(__name__)

# TODO: review this code

def destroy_tree(tree):
    root = tree.getroot()

    node_tracker = {root: [0, None]}

    for node in root.iterdescendants():
        parent = node.getparent()
        node_tracker[node] = [node_tracker[parent][0] + 1, parent]

    node_tracker = sorted([(depth, parent, child) for child, (depth, parent)
                           in node_tracker.items()], key=lambda x: x[0], reverse=True)

    for _, parent, child in node_tracker:
        if parent is None:
            break
        parent.remove(child)

    del tree

class XMLStreamExtractor:
    def __init__(self, xpath_expression):
        """
        Initialize the XML stream extractor with a specific XPath expression.
        
        :param xpath_expression: XPath string to select desired elements
        """
        self.xpath_expression = self._convert_xpath_to_namespace_agnostic(xpath_expression)

    def _convert_xpath_to_namespace_agnostic(self, xpath):
        """
        Convert standard XPath to use local-name() for namespace agnostic matching
        """
        # Split the XPath into parts

        has_name = False
        # add a special case for the name(expression) case. this is a major hack
        if xpath.startswith('name(') and xpath.endswith(')'):
            xpath = xpath[len('name('):-len(')')]
            has_name = True

        parts = xpath.split('/')
        converted_parts = []

        for part in parts:
            if part == '':
                converted_parts.append('')
            elif part == '*':
                converted_parts.append('*')
            elif '[' in part:
                # Handle predicates
                element_name, predicate = part.split('[', 1)
                converted = f"*[local-name()='{element_name}'][{predicate}"
                converted_parts.append(converted)
            elif '()' in part or '@' in part:
                converted_parts.append(part)
            else:
                converted = f"*[local-name()='{part}']"
                converted_parts.append(converted)

        xpath = '/'.join(converted_parts)
        if has_name:
            xpath = f'name({xpath})'
        return xpath

    def extract_elements(self, xml_txt, return_elems):
        """
        Extract unique elements from XML using a streaming parser.
        
        :param xml_source: File path or file-like object containing XML
        :return: List of unique extracted elements
        """
        results_map = {}
        errors = []
        
        # Create an iterative parser that's tolerant of ill-formed XML
        context = etree.iterparse(
            io.BytesIO(xml_txt.encode('utf8')), 
            events=('end',),  # Only process end events
            tag='*',  # Match all tags
            recover=False,  # Try to recover from parsing errors
            huge_tree=True  # Handle very large XML files
        )
        
        try:
            for event, elem in context:
                # Find matching elements using XPath
                matching_elements = elem.xpath(self.xpath_expression)
                
                # Process matching elements
                for match in matching_elements:
                    if isinstance(match, etree._Element):
                        # Convert element to string, stripping whitespace
                        result = etree.tostring(match, encoding='unicode', method='xml').strip()
                    else:
                        # Convert non-element matches to string
                        result = str(match).strip()
                    
                    # Add to map (automatically prevents duplicates)
                    if result not in results_map:
                        results_map[result] = match
                
                # Cleanup to prevent memory buildup
                elem.clear()
        
        except etree.XMLSyntaxError as e:
            errors.append(f"XML Syntax Error: {e}")
        finally:
            # Ensure context is closed
            try:
                context.close()
            except Exception:
                pass
        
        if return_elems:
            return list(results_map.values()), errors

        return list(results_map.keys()), errors


def extract_xml_elements(xml_txt, xpath, return_elems=False):
    """
    Convenience function to extract unique XML elements.
    
    :param xml_path: Path to XML file or file-like object
    :param xpath: XPath expression to select elements
    :return: List of unique extracted elements
    """
    extractor = XMLStreamExtractor(xpath)
    return extractor.extract_elements(xml_txt, return_elems)


