package com.archon.jdbsee.jdbcli.format

import com.archon.jdbsee.jdbcli.enums.MetadataLevel
import com.archon.jdbsee.jdbcli.enums.OutputFormat
import com.archon.jdbsee.jdbcli.model.QueryResult
import com.archon.jdbsee.jdbcli.security.ValidationResult

interface AiResponseFormatter {
  String formatSuccess(QueryResult result, OutputFormat format, MetadataLevel metadataLevel)
  String formatError(ValidationResult validationResult, String sql)
  String formatException(Exception e, String context)
}
