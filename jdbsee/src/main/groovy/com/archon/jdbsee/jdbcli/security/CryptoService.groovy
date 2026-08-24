package com.archon.jdbsee.jdbcli.security

import com.archon.jdbsee.jdbcli.ConfigService
import com.archon.jdbsee.jdbcli.enums.ErrorCode
import com.archon.jdbsee.jdbcli.exception.CryptoException
import org.slf4j.Logger
import org.slf4j.LoggerFactory

import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import javax.inject.Inject
import javax.inject.Singleton
import java.nio.file.FileStore
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.attribute.AclEntry
import java.nio.file.attribute.AclEntryPermission
import java.nio.file.attribute.AclEntryType
import java.nio.file.attribute.AclFileAttributeView
import java.nio.file.attribute.PosixFilePermission
import java.security.SecureRandom
import java.util.Arrays
import java.util.Base64
import java.util.EnumSet

/**
 * AES-256-GCM encryption of sensitive values (e.g. alias passwords).
 *
 * The 256-bit master key is stored in a file (owner-only permissions) under
 * the config folder and lazily created on first use. Encrypted values are
 * prefixed with {@link #CIPHER_PREFIX} so they can be told apart from legacy
 * plaintext values.
 */
@Singleton
class CryptoService {
  private static final Logger LOG = LoggerFactory.getLogger(CryptoService)
  private static final String KEY_FILE_NAME = '.master.key'
  private static final int KEY_LENGTH = 32 // 256 bits
  private static final String CIPHER_PREFIX = '{enc:v1}'
  private static final String AES_GCM = 'AES/GCM/NoPadding'
  private static final int NONCE_LENGTH = 12
  private static final int TAG_BITS = 128
  ConfigService configService
  private volatile byte[] keyBytes

  CryptoService() {
  }

  @Inject
  CryptoService(ConfigService configService) {
    this.configService = configService
  }

  void setConfigService(ConfigService configService) {
    this.configService = configService
  }

  synchronized void ensureKeyLoaded() {
    if (keyBytes != null) {
      return
    }
    File keyFile = keyFile()
    if (keyFile.exists()) {
      byte[] bytes
      try {
        bytes = keyFile.bytes
      } catch (IOException e) {
        throw new CryptoException(ErrorCode.KEY_FILE_UNREADABLE, e.message, e)
      }
      if (bytes.length != KEY_LENGTH) {
        throw new CryptoException(ErrorCode.KEY_FILE_CORRUPTED, "expected $KEY_LENGTH bytes at ${keyFile.absolutePath}, got ${bytes.length}")
      }
      if (bytes.every { it == 0 }) {
        throw new CryptoException(ErrorCode.KEY_FILE_CORRUPTED, "key file contains all zeros at ${keyFile.absolutePath}")
      }
      keyBytes = bytes
      return
    }
    keyBytes = generateKeyFile(keyFile)
  }

  byte[] getKey() {
    ensureKeyLoaded()
    keyBytes.clone()
  }

  private File keyFile() {
    new File(configService.userFolder, KEY_FILE_NAME)
  }

  private byte[] generateKeyFile(File keyFile) {
    keyFile.parentFile.mkdirs()
    byte[] buf = new byte[KEY_LENGTH]
    SecureRandom.getInstanceStrong().nextBytes(buf)
    Path target = keyFile.toPath()
    Path tmp = Files.createTempFile(target.parent, KEY_FILE_NAME, '.tmp')
    try {
      Files.write(tmp, buf)
      applyOwnerOnlyPermissions(tmp)
      Files.move(tmp, target, StandardCopyOption.ATOMIC_MOVE)
      LOG.info('生成新的主密钥文件 {}', target)
      return buf
    } finally {
      Files.deleteIfExists(tmp)
    }
  }

  private void applyOwnerOnlyPermissions(Path path) {
    FileStore store = Files.getFileStore(path)
    if (store.supportsFileAttributeView('posix')) {
      Files.setPosixFilePermissions(path, EnumSet.of(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE))
      return
    }
    AclFileAttributeView view = Files.getFileAttributeView(path, AclFileAttributeView)
    if (view != null) {
      try {
        AclEntry entry = AclEntry.newBuilder()
            .setType(AclEntryType.ALLOW)
            .setPrincipal(view.owner)
            .setPermissions(EnumSet.allOf(AclEntryPermission))
            .build()
        view.setAcl([entry])
      } catch (IOException e) {
        LOG.warn('Windows ACL 设置失败，降级为只读属性: {}', e.message)
        path.toFile().setReadable(true, true)
        path.toFile().setWritable(true, true)
      }
      return
    }
    LOG.warn('当前文件系统既不支持 POSIX 也不支持 ACL，密钥文件权限可能不安全：{}', path)
  }

  String encrypt(String plain) {
    if (plain == null) {
      return null
    }
    if (plain.isEmpty()) {
      return ''
    }
    byte[] nonce = new byte[NONCE_LENGTH]
    SecureRandom.getInstanceStrong().nextBytes(nonce)
    try {
      Cipher cipher = Cipher.getInstance(AES_GCM)
      cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(getKey(), 'AES'), new GCMParameterSpec(TAG_BITS, nonce))
      byte[] ct = cipher.doFinal(plain.getBytes('UTF-8'))
      byte[] payload = new byte[NONCE_LENGTH + ct.length]
      System.arraycopy(nonce, 0, payload, 0, NONCE_LENGTH)
      System.arraycopy(ct, 0, payload, NONCE_LENGTH, ct.length)
      return CIPHER_PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(payload)
    } catch (CryptoException e) {
      throw e
    } catch (Exception e) {
      throw new CryptoException(ErrorCode.ENCRYPT_FAILED, e.message, e)
    }
  }

  String decrypt(String cipherText) {
    if (cipherText == null) {
      return null
    }
    if (cipherText.isEmpty()) {
      return ''
    }
    try {
      String payloadB64 = cipherText.startsWith(CIPHER_PREFIX) ? cipherText.substring(CIPHER_PREFIX.length()) : cipherText
      byte[] payload = Base64.getUrlDecoder().decode(payloadB64)
      byte[] nonce = Arrays.copyOfRange(payload, 0, NONCE_LENGTH)
      byte[] ct = Arrays.copyOfRange(payload, NONCE_LENGTH, payload.length)
      Cipher cipher = Cipher.getInstance(AES_GCM)
      cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(getKey(), 'AES'), new GCMParameterSpec(TAG_BITS, nonce))
      return new String(cipher.doFinal(ct), 'UTF-8')
    } catch (CryptoException e) {
      throw e
    } catch (Exception e) {
      throw new CryptoException(ErrorCode.DECRYPT_FAILED, e.message, e)
    }
  }

  String decryptIfNeeded(String value) {
    if (value == null || value.isEmpty()) {
      return value
    }
    if (!value.startsWith(CIPHER_PREFIX)) {
      return value
    }
    decrypt(value)
  }

  static boolean isCiphered(String value) {
    value != null && value.startsWith(CIPHER_PREFIX)
  }
}
