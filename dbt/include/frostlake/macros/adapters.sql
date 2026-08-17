{#
  Frostlake's adapter macros. dbt's global project supplies the rest; these are the
  ones whose default implementation raises, plus a few where Frostlake's dialect
  differs from the cross-adapter default.
#}

{% macro frostlake__current_timestamp() -%}
    CURRENT_TIMESTAMP()
{%- endmacro %}


{% macro frostlake__snapshot_string_as_time(timestamp) -%}
    {%- set result = "TO_TIMESTAMP_NTZ('" ~ timestamp ~ "')" -%}
    {{ return(result) }}
{%- endmacro %}


{% macro frostlake__snapshot_get_time() -%}
    CURRENT_TIMESTAMP()::TIMESTAMP_NTZ
{%- endmacro %}


{% macro frostlake__list_schemas(database) -%}
    {% call statement('list_schemas', fetch_result=True, auto_begin=False) -%}
        select schema_name
        from {{ information_schema_name(database) }}.schemata
        where upper(catalog_name) = upper('{{ database }}')
    {%- endcall %}
    {{ return(load_result('list_schemas').table) }}
{%- endmacro %}


{% macro frostlake__check_schema_exists(information_schema, schema) -%}
    {% call statement('check_schema_exists', fetch_result=True, auto_begin=False) -%}
        select count(*)
        from {{ information_schema }}.schemata
        where upper(schema_name) = upper('{{ schema }}')
          and upper(catalog_name) = upper('{{ information_schema.database }}')
    {%- endcall %}
    {{ return(load_result('check_schema_exists').table) }}
{%- endmacro %}


{% macro frostlake__list_relations_without_caching(schema_relation) %}
    {% call statement('list_relations_without_caching', fetch_result=True) -%}
        select
            table_catalog as database,
            table_name as name,
            table_schema as schema,
            case
                when table_type = 'VIEW' then 'view'
                else 'table'
            end as type
        from {{ schema_relation.information_schema() }}.tables
        where upper(table_schema) = upper('{{ schema_relation.schema }}')
    {%- endcall %}
    {{ return(load_result('list_relations_without_caching').table) }}
{% endmacro %}


{% macro frostlake__get_columns_in_relation(relation) -%}
    {% call statement('get_columns_in_relation', fetch_result=True) %}
        select
            column_name,
            data_type,
            character_maximum_length,
            numeric_precision,
            numeric_scale
        from {{ relation.information_schema() }}.columns
        where upper(table_name) = upper('{{ relation.identifier }}')
          {% if relation.schema %}
          and upper(table_schema) = upper('{{ relation.schema }}')
          {% endif %}
        order by ordinal_position
    {% endcall %}
    {% set table = load_result('get_columns_in_relation').table %}
    {{ return(sql_convert_columns_in_relation(table)) }}
{%- endmacro %}


{% macro frostlake__get_catalog(information_schema, schemas) -%}
    {#-
      What `dbt docs generate` reads. The aliases are quoted so they survive as
      lowercase; unquoted they would come back uppercased and dbt would not find them.
    -#}
    {% set query %}
        select
            t.table_catalog as "table_database",
            t.table_schema as "table_schema",
            t.table_name as "table_name",
            case when t.table_type = 'VIEW' then 'view' else 'table' end as "table_type",
            null as "table_comment",
            null as "table_owner",
            c.column_name as "column_name",
            c.ordinal_position as "column_index",
            c.data_type as "column_type",
            null as "column_comment"
        from {{ information_schema }}.tables t
        join {{ information_schema }}.columns c
          on c.table_catalog = t.table_catalog
         and c.table_schema = t.table_schema
         and c.table_name = t.table_name
        where (
            {%- for schema in schemas -%}
                upper(t.table_schema) = upper('{{ schema }}')
                {%- if not loop.last %} or {% endif -%}
            {%- endfor -%}
        )
        order by "table_schema", "table_name", "column_index"
    {% endset %}
    {{ return(run_query(query)) }}
{%- endmacro %}


{% macro frostlake__create_schema(relation) -%}
    {%- call statement('create_schema') -%}
        create schema if not exists {{ relation.without_identifier() }}
    {%- endcall -%}
{% endmacro %}


{% macro frostlake__drop_schema(relation) -%}
    {%- call statement('drop_schema') -%}
        drop schema if exists {{ relation.without_identifier() }} cascade
    {%- endcall -%}
{% endmacro %}


{% macro frostlake__create_table_as(temporary, relation, compiled_code, language='sql') -%}
    {%- if language != 'sql' -%}
        {% do exceptions.raise_compiler_error("Frostlake only supports SQL models, got " ~ language) %}
    {%- endif -%}
    create or replace {% if temporary -%}temporary {% endif -%}table {{ relation }}
    as (
        {{ compiled_code }}
    )
{%- endmacro %}


{% macro frostlake__create_view_as(relation, sql) -%}
    create or replace view {{ relation }} as (
        {{ sql }}
    )
{%- endmacro %}


{% macro frostlake__rename_relation(from_relation, to_relation) -%}
    {#-
      A view has to be renamed as a view, not as a table. The engine also rejects a
      qualified target for ALTER VIEW (it accepts one for ALTER TABLE), so views are
      renamed by bare identifier — the rename stays within the source schema.
    -#}
    {% call statement('rename_relation') -%}
        {%- if from_relation.type == 'view' -%}
            alter view {{ from_relation }} rename to {{ to_relation.identifier }}
        {%- else -%}
            alter table {{ from_relation }} rename to {{ to_relation }}
        {%- endif -%}
    {%- endcall %}
{% endmacro %}


{% macro frostlake__drop_relation(relation) -%}
    {#- the engine takes CASCADE on DROP SCHEMA but not on DROP TABLE/VIEW -#}
    {% call statement('drop_relation', auto_begin=False) -%}
        drop {{ relation.type }} if exists {{ relation }}
    {%- endcall %}
{% endmacro %}


{% macro frostlake__truncate_relation(relation) -%}
    {% call statement('truncate_relation') -%}
        truncate table {{ relation }}
    {%- endcall %}
{% endmacro %}


{% macro frostlake__alter_column_type(relation, column_name, new_column_type) -%}
    {% call statement('alter_column_type') %}
        alter table {{ relation }} alter column {{ adapter.quote(column_name) }} set data type {{ new_column_type }}
    {% endcall %}
{% endmacro %}


{% macro frostlake__alter_relation_comment(relation, relation_comment) -%}
    comment on {{ relation.type }} {{ relation }} is $${{ relation_comment | replace('$', '[$]') }}$$;
{%- endmacro %}


{% macro frostlake__alter_column_comment(relation, column_dict) -%}
    {% for column_name in column_dict %}
        {%- set comment = column_dict[column_name]['description'] -%}
        comment on column {{ relation }}.{{ adapter.quote(column_name) }} is $${{ comment | replace('$', '[$]') }}$$;
    {% endfor %}
{%- endmacro %}
