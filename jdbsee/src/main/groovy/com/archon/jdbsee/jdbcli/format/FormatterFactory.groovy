package com.archon.jdbsee.jdbcli.format

import com.archon.jdbsee.jdbcli.enums.OutputFormat
import groovy.util.logging.Slf4j

import javax.inject.Inject
import javax.inject.Singleton

/**
 * Resolves the {@link AiResponseFormatter} implementation for a given output format.
 */
@Singleton
@Slf4j
class FormatterFactory {

  private JsonAiResponseFormatter jsonFormatter
  private MarkdownAiResponseFormatter markdownFormatter
  private CsvAiResponseFormatter csvFormatter

  @Inject
  FormatterFactory(JsonAiResponseFormatter jsonFormatter, MarkdownAiResponseFormatter markdownFormatter, CsvAiResponseFormatter csvFormatter) {
    this.jsonFormatter = jsonFormatter
    this.markdownFormatter = markdownFormatter
    this.csvFormatter = csvFormatter
  }

  FormatterFactory() {
  }

  AiResponseFormatter getFormatter(OutputFormat format) {
    switch (format) {
      case OutputFormat.JSON:
        return jsonFormatter
      case OutputFormat.MARKDOWN:
        return markdownFormatter
      case OutputFormat.CSV:
        return csvFormatter
      default:
        log.warn('Unknown output format: {}, defaulting to JSON', format)
        return jsonFormatter
    }
  }

  AiResponseFormatter getFormatter(String formatCode) {
    OutputFormat format = OutputFormat.fromCode(formatCode)
    return getFormatter(format)
  }

  AiResponseFormatter getFormatterFromString(String formatCode) {
    return getFormatter(formatCode)
  }
}
