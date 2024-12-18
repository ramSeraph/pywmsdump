import io
import logging
from lxml import etree

logger = logging.getLogger(__name__)

# TODO: review this code

class XMLStreamExtractor:
    def __init__(self, xpath_expression):
        """
        Initialize the XML stream extractor with a specific XPath expression.
        
        :param xpath_expression: XPath string to select desired elements
        """
        self.xpath_expression = xpath_expression

    def extract_elements(self, xml_txt):
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
                        results_map[result] = True
                
                # Cleanup to prevent memory buildup
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
        
        except etree.XMLSyntaxError as e:
            errors.append(f"XML Syntax Error: {e}")
        finally:
            # Ensure context is closed
            try:
                context.close()
            except Exception:
                pass
        
        return list(results_map.keys()), errors

def extract_xml_elements(xml_txt, xpath):
    """
    Convenience function to extract unique XML elements.
    
    :param xml_path: Path to XML file or file-like object
    :param xpath: XPath expression to select elements
    :return: List of unique extracted elements
    """
    extractor = XMLStreamExtractor(xpath)
    return extractor.extract_elements(xml_txt)


