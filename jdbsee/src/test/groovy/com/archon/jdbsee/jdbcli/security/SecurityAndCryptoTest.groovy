package com.archon.jdbsee.jdbcli.security

import com.archon.jdbsee.jdbcli.ConfigService
import com.archon.jdbsee.jdbcli.enums.ErrorCode
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

import static org.junit.Assert.*

class SecurityAndCryptoTest {

  @Rule
  public TemporaryFolder temporaryFolder = new TemporaryFolder()

  @Test
  void queryValidatorAllowsReadsAndRejectsWritesAndLocks() {
    def validator = new SqlSecurityValidator()

    assertTrue(validator.validate('SELECT id FROM users WHERE id = 1').valid)
    assertEquals(ErrorCode.INVALID_STATEMENT_TYPE.code,
        validator.validate("UPDATE users SET name = 'x'").errorCode)
    assertEquals(ErrorCode.READ_ONLY_VIOLATION.code,
        validator.validate('SELECT id FROM users FOR UPDATE').errorCode)
    assertEquals(ErrorCode.DANGEROUS_PATTERN.code,
        validator.validate('SELECT 1; -- injected').errorCode)
  }

  @Test
  void executeValidatorAllowsDmlAndRejectsSelectAndDestructiveDdl() {
    def validator = new DmlSecurityValidator()

    assertTrue(validator.validate("INSERT INTO users(id) VALUES (1)").valid)
    assertEquals(ErrorCode.SELECT_NOT_ALLOWED.code,
        validator.validate('SELECT * FROM users').errorCode)
    assertEquals(ErrorCode.DANGEROUS_DDL.code,
        validator.validate('TRUNCATE TABLE users').errorCode)
    assertEquals(ErrorCode.DANGEROUS_DDL.code,
        validator.validate('DROP DATABASE app').errorCode)
  }

  @Test
  void cryptoRoundTripsWithAStableOwnerOnlyKey() {
    File userFolder = temporaryFolder.newFolder('crypto-home')
    def crypto = new CryptoService(configFor(userFolder))

    String first = crypto.encrypt('s3cret')
    String second = crypto.encrypt('s3cret')

    assertTrue(CryptoService.isCiphered(first))
    assertNotEquals(first, second)
    assertEquals('s3cret', crypto.decrypt(first))
    assertEquals('plain', crypto.decryptIfNeeded('plain'))
    assertEquals(32, new File(userFolder, '.master.key').length())
  }

  private static ConfigService configFor(File userFolder) {
    new ConfigService() {
      @Override
      File getUserFolder() {
        userFolder
      }
    }
  }
}
