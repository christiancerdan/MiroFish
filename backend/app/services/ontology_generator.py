"""
本体生成服务
接口1：分析文本内容，生成适合社会模拟的实体和关系类型定义
"""

import re
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction
from ..utils.file_parser import split_text_into_chunks
from ..utils.ontology import (
    MAX_ONTOLOGY_TYPES,
    MAX_ONTOLOGY_ATTRIBUTES,
    MAX_ONTOLOGY_SOURCE_TARGETS,
    RESERVED_ONTOLOGY_ATTRIBUTE_NAMES,
)


ONTOLOGY_SYSTEM_PROMPT = """Design a concise ontology for social-media simulation from the supplied source documents.
Return only one complete JSON object with exactly these keys:
{"entity_types":[{"name":"Person","description":"An individual participant.","attributes":[],"examples":[]}],"edge_types":[],"analysis_summary":"Brief source-grounded rationale."}

Choose the smallest useful taxonomy supported by the source:
- Define 1–10 actor TYPES, not one type per person. A few people or reader personas usually need only Person; represent different roles as attributes. Use Organization only when organizations occur. More specific types are optional when the source needs distinct actor categories. Never fill a quota or invent participants, organizations, facts, or examples.
- Actors must be identifiable individuals, organizations, or explicitly supplied fictional personas able to speak or interact. Preserve their assumed/fictional status in descriptions or attributes; never present them as observed real people. Headlines, topics, opinions, abstract concepts and the simulation task are not actors. Do not label actors Entity or Node.
- Define 0–10 relationship types grounded in actual source relationships. Use an empty edge_types array when none are evidenced; shared presence in a document does not imply a relationship.
- Each entity has exactly name, description, attributes, examples. Each edge has exactly name, description, attributes, source_targets. An edge's source_targets contains 1–10 distinct {"source":"DeclaredType","target":"DeclaredType"} pairs using declared entity names.
- Entity names are English PascalCase; edge names are UPPER_SNAKE_CASE. Names and descriptions are at most 100 characters. Names are unique within each list.
- Prefer 0–2 attributes per type. Each attribute has exactly name, type (always "text"), description. Attribute names are English snake_case, unique within that type, and cannot be name, uuid, group_id, graph_id, name_embedding, created_at, summary. Empty attributes arrays are valid.
- Use at most two short examples from the source per entity, or [] when none is appropriate. Examples are instances, not extra types.
- Keep descriptions and analysis_summary brief (summary at most 500 characters). Preserve the output language instruction for prose. Do not add commentary, markdown or extra keys.
"""

ONTOLOGY_VALIDATION_FEEDBACK = (
    "Return the complete ontology object again, using the smallest source-grounded taxonomy. "
    "Required keys: entity_types (1–10 objects), edge_types (0–10 objects), analysis_summary "
    "(nonempty string, at most 500 characters). Entity fields: name, description, attributes, "
    "examples. Edge fields: name, description, attributes, source_targets. Names must be unique; "
    "every edge endpoint must reference a declared actor type. Omit unsupported relationships "
    "instead of inventing them. Empty attributes and examples arrays are allowed."
)


class OntologyGenerator:
    """
    本体生成器
    分析文本内容，生成实体和关系类型定义
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        生成本体定义
        
        Args:
            document_texts: 文档文本列表
            simulation_requirement: 模拟需求描述
            additional_context: 额外上下文
            
        Returns:
            本体定义（entity_types, edge_types等）
        """
        # 构建用户消息
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context
        )
        
        lang_instruction = get_language_instruction()
        system_prompt = f"{ONTOLOGY_SYSTEM_PROMPT}\n\n{lang_instruction}\nUse the specified language for descriptions, examples and analysis_summary; keep schema keys and type/attribute names in English."
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        # 调用LLM
        # Parsing and schema failures share a single bounded retry budget. Explicit
        # caps include hidden reasoning on providers that count it as completion.
        model_limit = getattr(self.llm_client, "token_limit", 16384)
        return self.llm_client.chat_json(
            messages=messages,
            temperature=0.3,
            max_tokens=min(8192, model_limit),
            retry_max_tokens=min(16384, model_limit),
            max_attempts=2,
            validator=self._validate_and_process,
            validation_feedback=ONTOLOGY_VALIDATION_FEEDBACK,
        )

    # 传给 LLM 的文本最大长度（5万字）
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    LONG_TEXT_CHUNK_SIZE = 8000
    LONG_TEXT_CHUNK_OVERLAP = 200
    MAX_LONG_TEXT_CHUNKS = 60
    MIN_LONG_TEXT_EXCERPT = 400
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """构建用户消息"""
        
        combined_text = self._build_document_context(document_texts)
        
        message = f"""## 模拟需求

{simulation_requirement}

## 文档内容

{combined_text}
"""
        
        if additional_context:
            message += f"""
## 额外说明

{additional_context}
"""
        
        message += """
Design only the actor types and relationships supported by these documents.
Use the smallest useful ontology: one Person type can cover several individual personas;
keep edge_types empty if the source states no relationships. Do not invent extra types to
reach a target count. Return the complete JSON object with concise prose.
"""

        return message

    def _build_document_context(self, document_texts: List[str]) -> str:
        """构建用于本体分析的文档上下文，长文本按全局分块抽样而不是只截取开头。"""

        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)

        if original_length <= self.MAX_TEXT_LENGTH_FOR_LLM:
            return combined_text

        chunks = self._collect_document_chunks(document_texts)
        if not chunks:
            return ""

        selected_chunks = self._select_representative_chunks(chunks)
        excerpt_budget = self._calculate_excerpt_budget(len(selected_chunks))
        context = self._render_chunked_context(
            selected_chunks=selected_chunks,
            original_length=original_length,
            total_chunks=len(chunks),
            excerpt_limit=excerpt_budget,
        )

        while len(context) > self.MAX_TEXT_LENGTH_FOR_LLM and excerpt_budget > self.MIN_LONG_TEXT_EXCERPT:
            excerpt_budget = max(self.MIN_LONG_TEXT_EXCERPT, int(excerpt_budget * 0.85))
            context = self._render_chunked_context(
                selected_chunks=selected_chunks,
                original_length=original_length,
                total_chunks=len(chunks),
                excerpt_limit=excerpt_budget,
            )

        if len(context) > self.MAX_TEXT_LENGTH_FOR_LLM:
            marker = "\n\n...(分块上下文已压缩到本体分析长度限制内)..."
            context = context[:self.MAX_TEXT_LENGTH_FOR_LLM - len(marker)] + marker

        return context

    def _collect_document_chunks(self, document_texts: List[str]) -> List[Dict[str, Any]]:
        """按文档收集分块，保留文档和分块编号方便提示词定位。"""

        all_chunks: List[Dict[str, Any]] = []
        for doc_index, text in enumerate(document_texts, 1):
            doc_chunks = split_text_into_chunks(
                text,
                chunk_size=self.LONG_TEXT_CHUNK_SIZE,
                overlap=self.LONG_TEXT_CHUNK_OVERLAP,
            )
            total_doc_chunks = len(doc_chunks)
            for chunk_index, chunk in enumerate(doc_chunks, 1):
                all_chunks.append({
                    "document_index": doc_index,
                    "chunk_index": chunk_index,
                    "total_document_chunks": total_doc_chunks,
                    "text": chunk,
                })

        return all_chunks

    def _select_representative_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """从全部分块中等距抽样，覆盖长文开头、中段和结尾。"""

        if len(chunks) <= self.MAX_LONG_TEXT_CHUNKS:
            return chunks

        if self.MAX_LONG_TEXT_CHUNKS <= 1:
            return [chunks[0]]

        last_index = len(chunks) - 1
        selected_indexes = {
            round(i * last_index / (self.MAX_LONG_TEXT_CHUNKS - 1))
            for i in range(self.MAX_LONG_TEXT_CHUNKS)
        }
        return [chunks[i] for i in sorted(selected_indexes)]

    def _calculate_excerpt_budget(self, selected_count: int) -> int:
        """根据选中的分块数量为每块分配字符预算。"""

        header_budget = 600
        chunk_header_budget = 120 * selected_count
        available = max(
            self.MIN_LONG_TEXT_EXCERPT * selected_count,
            self.MAX_TEXT_LENGTH_FOR_LLM - header_budget - chunk_header_budget,
        )
        return max(self.MIN_LONG_TEXT_EXCERPT, available // max(selected_count, 1))

    def _render_chunked_context(
        self,
        selected_chunks: List[Dict[str, Any]],
        original_length: int,
        total_chunks: int,
        excerpt_limit: int,
    ) -> str:
        """渲染长文本分块上下文。"""

        lines = [
            (
                f"【长文本自动分块摘要】原文共{original_length}字，"
                f"已分为{total_chunks}个文本块用于全局覆盖分析。"
            ),
            (
                f"以下展示其中{len(selected_chunks)}个代表性文本块的摘录，"
                "覆盖开头、中段和结尾；请基于这些跨全文线索设计本体，不要只依赖第一段内容。"
            ),
        ]

        for chunk in selected_chunks:
            excerpt = self._excerpt_text(chunk["text"], excerpt_limit)
            lines.append(
                "\n".join([
                    (
                        f"--- 文档 {chunk['document_index']} / "
                        f"分块 {chunk['chunk_index']}/{chunk['total_document_chunks']} ---"
                    ),
                    excerpt,
                ])
            )

        return "\n\n".join(lines)

    @staticmethod
    def _excerpt_text(text: str, char_limit: int) -> str:
        """长分块保留首尾，避免每个分块内部再次变成只看开头。"""

        text = text.strip()
        if len(text) <= char_limit:
            return text

        marker = "\n...(本分块中间内容省略)...\n"
        if char_limit <= len(marker) + 20:
            return text[:char_limit]

        remaining = char_limit - len(marker)
        head_len = remaining // 2
        tail_len = remaining - head_len
        return f"{text[:head_len].rstrip()}{marker}{text[-tail_len:].lstrip()}"
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a complete response without inventing or discarding graph types.

        Errors contain schema paths, never source/model prose, so they are safe
        to feed into the shared client's bounded regeneration request.
        """
        def object_fields(value, fields, path):
            if not isinstance(value, dict) or set(value) != set(fields):
                raise ValueError(f"{path} must be an object with exactly: {', '.join(fields)}")

        def text(value, path, limit=100):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{path} must be nonempty text of at most {limit} characters")
            return value.strip()

        def bounded_list(value, path, minimum, maximum):
            if not isinstance(value, list) or not minimum <= len(value) <= maximum:
                raise ValueError(f"{path} must be an array with {minimum}–{maximum} items")
            return value

        def identifier(value, path, pattern):
            value = text(value, path)
            if not re.fullmatch(pattern, value):
                raise ValueError(f"{path} has invalid identifier casing or characters")
            return value

        def attributes(value, path):
            definitions = []
            names = set()
            for index, attribute in enumerate(bounded_list(value, path, 0, MAX_ONTOLOGY_ATTRIBUTES)):
                attr_path = f"{path}[{index}]"
                object_fields(attribute, ("name", "type", "description"), attr_path)
                name = identifier(attribute["name"], f"{attr_path}.name", r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
                if name in RESERVED_ONTOLOGY_ATTRIBUTE_NAMES or name in names:
                    raise ValueError(f"{attr_path}.name must be unique and not reserved")
                if attribute["type"] != "text":
                    raise ValueError(f"{attr_path}.type must be text")
                definitions.append({
                    "name": name,
                    "type": "text",
                    "description": text(attribute["description"], f"{attr_path}.description"),
                })
                names.add(name)
            return definitions

        object_fields(result, ("entity_types", "edge_types", "analysis_summary"), "ontology")
        summary = text(result["analysis_summary"], "analysis_summary", 500)
        raw_entities = bounded_list(result["entity_types"], "entity_types", 1, MAX_ONTOLOGY_TYPES)
        raw_edges = bounded_list(result["edge_types"], "edge_types", 0, MAX_ONTOLOGY_TYPES)
        entity_names = set()
        entities = []
        for index, entity in enumerate(raw_entities):
            path = f"entity_types[{index}]"
            object_fields(entity, ("name", "description", "attributes", "examples"), path)
            name = identifier(entity["name"], f"{path}.name", r"[A-Z][A-Za-z0-9]*")
            if name in {"Entity", "Node"} or name.casefold() in entity_names:
                raise ValueError(f"{path}.name must be a unique actor type, not Entity or Node")
            entities.append({
                "name": name,
                "description": text(entity["description"], f"{path}.description"),
                "attributes": attributes(entity["attributes"], f"{path}.attributes"),
                "examples": [
                    text(example, f"{path}.examples[{example_index}]")
                    for example_index, example in enumerate(
                        bounded_list(entity["examples"], f"{path}.examples", 0, 10)
                    )
                ],
            })
            entity_names.add(name.casefold())

        declared_names = {entity["name"] for entity in entities}
        edge_names = set()
        edges = []
        for index, edge in enumerate(raw_edges):
            path = f"edge_types[{index}]"
            object_fields(edge, ("name", "description", "attributes", "source_targets"), path)
            name = identifier(edge["name"], f"{path}.name", r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")
            if name in edge_names:
                raise ValueError(f"{path}.name must be unique")
            pairs = []
            seen_pairs = set()
            for pair_index, pair in enumerate(bounded_list(
                edge["source_targets"], f"{path}.source_targets", 1, MAX_ONTOLOGY_SOURCE_TARGETS
            )):
                pair_path = f"{path}.source_targets[{pair_index}]"
                object_fields(pair, ("source", "target"), pair_path)
                source = text(pair["source"], f"{pair_path}.source")
                target = text(pair["target"], f"{pair_path}.target")
                # Entity is the legacy wildcard understood by both graph backends.
                if source not in declared_names | {"Entity"} or target not in declared_names | {"Entity"}:
                    raise ValueError(f"{pair_path} must reference declared types or the Entity wildcard")
                if (source, target) in seen_pairs:
                    raise ValueError(f"{pair_path} must be a unique endpoint pair")
                seen_pairs.add((source, target))
                pairs.append({"source": source, "target": target})
            edges.append({
                "name": name,
                "description": text(edge["description"], f"{path}.description"),
                "attributes": attributes(edge["attributes"], f"{path}.attributes"),
                "source_targets": pairs,
            })
            edge_names.add(name)

        return {"entity_types": entities, "edge_types": edges, "analysis_summary": summary}

    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        将本体定义转换为Python代码（类似ontology.py）
        
        Args:
            ontology: 本体定义
            
        Returns:
            Python代码字符串
        """
        code_lines = [
            '"""',
            '自定义实体类型定义',
            '由MiroFish自动生成，用于社会舆论模拟',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== 实体类型定义 ==============',
            '',
        ]
        
        # 生成实体类型
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== 关系类型定义 ==============')
        code_lines.append('')
        
        # 生成关系类型
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # 转换为PascalCase类名
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # 生成类型字典
        code_lines.append('# ============== 类型配置 ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # 生成边的source_targets映射
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)
