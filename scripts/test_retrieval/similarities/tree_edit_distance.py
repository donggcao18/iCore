import fnmatch
import math
import sqlite3
from typing import Tuple
from zss import Node as ZssNode, simple_distance
from pyan.node import Node
from pyan import CallGraphVisitor
import json
from ast import ClassDef, FunctionDef, AsyncFunctionDef

from scripts.config import REPO_ROOT_DIR

P1 = 0.1
P2 = 0.8

class WeightedZssNode(ZssNode):
    def __init__(self, node:Node, name=''):
        if node == None:
            super().__init__(name)
            self.func_node = None
            self.name = name
        else:
            super().__init__(node.get_name())
            self.func_node = node
            self.name = node.get_name()
        self.weight = P1 # Default minimum weight
    
    def __repr__(self):
        return f"{self.name} ({self.weight})"

    def set_weight(self, weight: float):
        self.weight = weight

    def total_weight(self):
        # Calculate the total weight of the node and its children
        total = self.weight
        for child in self.children:
            total += child.total_weight()
        return total

class WeightedTreeBuilder:
    def __init__(self, proj, instance_id, tree_path, keywords_path):
        self.proj = proj
        self.instance_id = instance_id
        self.keywords = self.load_keywords(keywords_path=keywords_path)

        # --- Database and DF Setup ---
        data_path = f"{tree_path}/{instance_id}"
        db_path = f"{data_path}/call_trees.db"
        df_path = f"{data_path}/df.json"
        
        # Establish a persistent connection to the database
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        with open(f"{tree_path}/{instance_id}/df.json") as f:
            df = json.load(f)
        self.total_documents = df['total_documents']
        self.df = df['df']
        self.max_idf = math.log10(self.total_documents) if self.total_documents > 1 else 1.0

    def set_tree_weight(self, node: WeightedZssNode, p1=P1):
        if node.func_node is not None:
            weight = self.get_node_weight(node.func_node.get_name(), p1)
        else:
            weight = self.get_node_weight(node.name, p1)
        node.set_weight(weight)
        for child in node.children:
            self.set_tree_weight(child)
        # Sort
        ordered_children = sorted(node.children, key=lambda c: c.name)
        node.children = ordered_children

    def load_keywords(self, keywords_path):
        with open(keywords_path) as f:
            keywords = json.load(f)
        return keywords[self.instance_id]
    
    def get_node_weight(self, node_full_name, p1=P1) -> float:
        """
        Calculates a node's weight. (with corrections)
        - Keywords get the max weight (1.0).
        - Others get a weight based on their global rarity (IDF).
        """
        # 1. Check if the function is a designated keyword

        for keyword in self.keywords:
            if '.' in keyword and fnmatch.fnmatch(keyword, node_full_name):
                return 1.0
            elif keyword == node_full_name.split('.')[-1]:
                return 1.0

        # 2. Calculate weight based on Inverse Document Frequency (IDF)
        doc_freq = self.df.get(node_full_name, 0)

        if doc_freq == 0 or doc_freq >= self.total_documents:
            return p1

        idf = math.log10(self.total_documents / (doc_freq + 1))
        normalized_weight = p1 + (0.9-p1) * idf / self.max_idf
        return max(p1, normalized_weight)
        
    
    def build_zss_tree(self, v: CallGraphVisitor, node: Node, depth=0, max_depth=5):
        if depth > max_depth or node is None:
            return None
        zss_node = WeightedZssNode(node)
        uses = v.uses_edges.get(node)
        uses = list(uses) if uses else []
        defs = v.defines_edges.get(node)
        defs = list(defs) if defs else []

        for child in sorted(uses + defs, key=lambda n: n.name):
            child_subtree = self.build_zss_tree(v, child, depth=depth+1, max_depth=max_depth)
            if child_subtree:
                zss_node.addkid(child_subtree)
        
        return zss_node

    def get_call_graph(self, files, root, function):
        # function: module.class.function
        try:
            v = CallGraphVisitor(files, root=root)
        except Exception as e:
            print(f"Error in CallGraphVisitor: {e}")
            return None
        function_name = function.split(".")[-1]
        function_namespace = ".".join(function.split(".")[:-1])
        node = v.get_node(function_namespace, function_name)
        use_tree = self.build_zss_tree(v, node, depth=0)
        if not use_tree.func_node.ast_node:
            return use_tree
        if isinstance(use_tree.func_node.ast_node, ClassDef):
            children = use_tree.children
            for child in children:
                if isinstance(child.func_node.ast_node, (FunctionDef, AsyncFunctionDef)) and 'test' in child.func_node.name:
                    use_tree = child
                    break
        assert isinstance(use_tree.func_node.ast_node, (FunctionDef, AsyncFunctionDef))
        use_tree.label = 'test_name'
        
        return use_tree

    def _reconstruct_tree_from_dict(self, data: dict) -> WeightedZssNode:
        """
        Recursively reconstructs a WeightedZssNode tree from a dictionary.
        """
        # Note: This creates a simplified Node object. For full fidelity,
        # your JSON would need to store more details about the original Node.
        # This implementation is sufficient if only the node name is needed.
        zss_node = WeightedZssNode(None, data['name'])
        
        for child_data in data.get('children', []):
            child_zss_node = self._reconstruct_tree_from_dict(child_data)
            zss_node.addkid(child_zss_node)
            
        return zss_node

    def get_call_graph_from_db(self, abs_file_path: str, rel_file_path: str, test_name: str):
        """
        Retrieves a serialized call tree from the SQLite database and reconstructs it.
        """
        try:
            # 1. Query the database for the specific test tree
            full_test_name = abs_file_path.replace(REPO_ROOT_DIR+'/', '').replace('.py', '').replace('/', '.') + '.' + test_name
            self.cursor.execute(
                "SELECT tree_json FROM call_trees WHERE test_name = ?",
                (full_test_name,)
            )
            # self.cursor.execute(
            #     "SELECT tree_json FROM call_trees",
            # )
            result = self.cursor.fetchone()

            # 2. If found, deserialize and reconstruct the tree
            if result:
                tree_json_str = result[0]
                tree_dict = json.loads(tree_json_str)
                return self._reconstruct_tree_from_dict(tree_dict)
            else:
                # 3. Return None if the tree is not in the database
                print(f"Warning: Tree not found in DB for {rel_file_path} -> {test_name}")
                return None
        except sqlite3.Error as e:
            print(f"Database error while fetching tree for {test_name}: {e}")
            return None

    def close_connection(self):
        """Closes the database connection."""
        if self.conn:
            self.conn.close()

def get_label(node):
    return (node.name, node.weight)
    
def label_distance(a, b) -> float:
    if isinstance(a, Tuple):
        a_label = a[0]
        a_weight = a[1]
    else:
        a_label = ''
        a_weight = 0.0
    if isinstance(b, Tuple):
        b_label = b[0]
        b_weight = b[1]
    else:
        b_label = ''
        b_weight = 0.0
    if a_label == b_label:
        return 0
    else:
        return 1.0 * (a_weight + b_weight)

def calc_tree_distance(gen_test_use_tree, repo_test_use_tree):
    return simple_distance(
        gen_test_use_tree, 
        repo_test_use_tree, 
        get_label=get_label,
        label_dist=label_distance,
    )